import googlemaps
import sqlite3
import time

def inicializar_tabela_resultados(db_path):
    """Cria a tabela de resultados para armazenar as coordenadas."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS geo_resultados (
            endereco_completo TEXT PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            precisao TEXT,
            place_id TEXT,
            data_processamento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def georeferenciar_com_trava(db_path, api_key, limite_maximo=39000):
    """
    Executa a geocodificação com uma trava de segurança para não exceder a cota.
    """
    gmaps = googlemaps.Client(key=api_key)
    
    inicializar_tabela_resultados(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query_pendentes = """
        SELECT DISTINCT endereco_completo 
        FROM view_para_geocodificar 
        WHERE endereco_completo NOT IN (SELECT endereco_completo FROM geo_resultados)
    """
    cursor.execute(query_pendentes)
    pendentes = cursor.fetchall()
    
    total_pendentes = len(pendentes)
    print(f"Encontrados {total_pendentes} endereços únicos para processar.")
    
    contador_sessao = 0
    
    for (endereco,) in pendentes:
        """ TRAVA DE SEGURANÇA """
        if contador_sessao >= limite_maximo:
            print(f"\n[AVISO] Limite de segurança de {limite_maximo} atingido. Interrompendo para poupar cota.")
            break

        try:
            geocode_result = gmaps.geocode(endereco, region='br')

            if geocode_result:
                res = geocode_result[0]
                lat = res['geometry']['location']['lat']
                lng = res['geometry']['location']['lng']
                precisao = res['geometry']['location_type']
                place_id = res['place_id']

                cursor.execute("""
                    INSERT OR IGNORE INTO geo_resultados 
                    (endereco_completo, latitude, longitude, precisao, place_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (endereco, lat, lng, precisao, place_id))
                
                conn.commit()
                contador_sessao += 1
                
                if contador_sessao % 10 == 0:
                    print(f"Processados: {contador_sessao}/{min(total_pendentes, limite_maximo)}")

            else:
                cursor.execute("""
                    INSERT OR IGNORE INTO geo_resultados (endereco_completo) VALUES (?)
                """, (endereco,))
                conn.commit()
                print(f"Endereço não localizado: {endereco[:50]}")

        except Exception as e:
            print(f"Erro ao processar '{endereco[:30]}': {e}")
            time.sleep(2)

    print(f"\nSessão finalizada. Total de requisições nesta rodada: {contador_sessao}")
    conn.close()