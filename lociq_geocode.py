import sqlite3
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from tqdm import tqdm

def carregar_dados(db_path, view_name):
    conn = sqlite3.connect(db_path)
    query = f"SELECT * FROM {view_name}"

    df = pd.read_sql(query, conn)

    conn.close()

    return df

def criar_cascata_enderecos(df):
    # Seleciona as colunas baseado na estrutura da view/csv
    cols = ['tipo_logradouro', 'logradouro', 'numero', 'bairro', 'municipio', 'uf']
    
    for col in cols:
        if col not in df.columns:
            df[col] = ''
    
    df_clean = df[cols].fillna('').astype(str)
    
    def build_levels(row):
        tl = row['tipo_logradouro'].strip()
        log = row['logradouro'].strip()
        num = row['numero'].strip()
        bai = row['bairro'].strip()
        mun = row['municipio'].strip()
        uf = row['uf'].strip()
        
        rua = f"{tl} {log}".strip()
        mun_uf = f"{mun} - {uf}".strip(" -")
        
        # Função auxiliar para evitar vírgulas duplas se algum campo estiver vazio
        def join_parts(*parts):
            valid_parts = [p for p in parts if p]
            return ", ".join(valid_parts)
            
        # 1 - Porta / 2 - Logradouro / 3 - Bairro / 4 - Município
        p1 = join_parts(f"{rua}, {num}" if rua and num else rua or num, bai, mun_uf)
        p2 = join_parts(rua, bai, mun_uf)
        p3 = join_parts(bai, mun_uf)
        p4 = mun_uf
        
        return pd.Series([p1, p2, p3, p4])
        
    df[['nivel_1_porta', 'nivel_2_logradouro', 'nivel_3_bairro', 'nivel_4_municipio']] = df_clean.apply(build_levels, axis=1)
    
    return df

def separar_unicos(df):
    # Mantém as combinações únicas considerando o endereço completo (nível 1)
    unique_df = df[['nivel_1_porta', 'nivel_2_logradouro', 'nivel_3_bairro', 'nivel_4_municipio']].drop_duplicates(subset=['nivel_1_porta']).copy()
    return unique_df

def geolocalizar(df):
    geolocator = Nominatim(user_agent="meu_projeto_bulk_v1")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1, max_retries=2)

    tqdm.pandas(desc="Geolocalizando")

    def attempt_geocode(row):
        # Tenta os níveis em cascata: 1 (Porta) -> 2 (Logradouro) -> 3 (Bairro) -> 4 (Município)
        niveis = ['nivel_1_porta', 'nivel_2_logradouro', 'nivel_3_bairro', 'nivel_4_municipio']
        
        for precisao, col in enumerate(niveis, start=1):
            addr = row[col]
            if not addr or addr.strip() == "":
                continue
            
            loc = geocode(addr)
            if loc:
                return pd.Series([loc.latitude, loc.longitude, precisao])
        
        # Se falhar em todos os 4 níveis
        return pd.Series([None, None, None])

    df[['latitude', 'longitude', 'precisao']] = df.progress_apply(attempt_geocode, axis=1)

    return df

def adiciona_ao_db(db_path, df):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = """
        CREATE TABLE IF NOT EXISTS enderecos_georreferenciados (
            endereco TEXT PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            precisao INTEGER
        )
    """
    cursor.execute(query)

    # Filtra os dados necessários
    lista_tuplas = df[['nivel_1_porta', 'latitude', 'longitude', 'precisao']].values.tolist()
    
    # Utiliza INSERT OR IGNORE para pular suavemente endereços já salvos
    cursor.executemany("INSERT OR IGNORE INTO enderecos_georreferenciados (endereco, latitude, longitude, precisao) VALUES (?, ?, ?, ?)", lista_tuplas)
    conn.commit()
    conn.close()


db_path = ''
view_name = ''


if __name__ == '__main__':
    df = carregar_dados(db_path, view_name)
    
    df = criar_cascata_enderecos(df)
    df_unicos = separar_unicos(df)
    df_geolocalizado = geolocalizar(df_unicos)
    
    adiciona_ao_db(db_path, df_geolocalizado)