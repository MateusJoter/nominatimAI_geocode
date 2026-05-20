import sqlite3
import pandas as pd
import time
import io
import os
from google import genai
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from tqdm import tqdm

# =============================================================================
# CONFIGURAÇÃO DA API DO GEMINI E DO NOMINATIM
# =============================================================================

# Chave do Gemini: Salve sua chave API do Gemini em um arquivo chamado "gemini_apikey.txt"
with open("gemini_apikey.txt", "r") as f:
    gemini_apikey = f.read().strip()

client = genai.Client(api_key=gemini_apikey)


def carregar_dados(db_path, view_name):
    conn = sqlite3.connect(db_path)
    query = f"SELECT * FROM {view_name}"

    df = pd.read_sql(query, conn)

    conn.close()

    return df

def tratar_logradouros_gemini(df):
    """
    Isola os logradouros únicos e envia para o Gemini tratar em uma única requisição,
    retornando um CSV que é mesclado de volta no DataFrame principal.
    """
    print("Iniciando tratamento de logradouros com Gemini...")
    
    # Extrai combinações únicas para não gastar tokens/limites repetindo as mesmas ruas
    df_unicos = df[['tipo_logradouro', 'logradouro']].drop_duplicates().copy()
    
    # Monta a string no formato CSV esperado
    csv_in = "tipo_logradouro;logradouro\n" + "\n".join(
        df_unicos['tipo_logradouro'].fillna('').astype(str) + ";" + 
        df_unicos['logradouro'].fillna('').astype(str)
    )

    prompt = f"""
    Trate e padronize as colunas 'tipo_logradouro' e 'logradouro' abaixo para otimizar buscas em geolocalizadores.
    Corrija abreviações, remova informações inúteis para geolocalização (como "PARTE A", "LOTE X", etc.) e padronize a grafia.
    Devolva APENAS os dados tratados no formato CSV separados por ';', com cabeçalho.
    Mantenha EXATAMENTE a mesma quantidade de linhas e a ordem original.
    Não inclua formatação markdown (como ```csv) nem qualquer texto além dos dados.

    Dados:
    {csv_in}
    """

    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite-preview',
            contents=prompt
        )
        
        texto_resposta = response.text.strip()
        
        # Limpa possível formatação residual de markdown caso a IA adicione
        if texto_resposta.startswith("```"):
            texto_resposta = "\n".join(texto_resposta.split("\n")[1:-1])

        df_tratado = pd.read_csv(io.StringIO(texto_resposta), sep=";")
        df_tratado.columns = ['tipo_logradouro_tratado', 'logradouro_tratado']
        
        # Junta o tratado com o único
        df_unicos = df_unicos.reset_index(drop=True)
        df_tratado = df_tratado.reset_index(drop=True)
        df_unicos['tipo_logradouro_tratado'] = df_tratado['tipo_logradouro_tratado']
        df_unicos['logradouro_tratado'] = df_tratado['logradouro_tratado']

        # Mescla de volta no dataframe principal
        df = df.merge(df_unicos, on=['tipo_logradouro', 'logradouro'], how='left')

        # Substitui os originais pelos tratados (se houver erro no merge, mantém o original)
        df['tipo_logradouro'] = df['tipo_logradouro_tratado'].fillna(df['tipo_logradouro'])
        df['logradouro'] = df['logradouro_tratado'].fillna(df['logradouro'])

        # Limpa as colunas auxiliares
        df = df.drop(columns=['tipo_logradouro_tratado', 'logradouro_tratado'])
        
        print("Tratamento concluído com sucesso!")

    except Exception as e:
        print(f"Erro ao processar com Gemini: {e}")
        print("A requisição falhou, continuando com os dados não tratados originais...")

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
    unique_df = df[['nivel_1_porta', 'nivel_2_logradouro', 'nivel_3_bairro', 'nivel_4_municipio']].drop_duplicates(subset=['nivel_1_porta']).copy()
    return unique_df

def geolocalizar(df):
    geolocator = Nominatim(user_agent="meu_projeto_bulk_v1")
    
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.25, max_retries=5)

    tqdm.pandas(desc="Geolocalizando com Nominatim")

    # Dicionário de descrições da precisão
    descricoes_precisao = {
        1: "1 - Porta",
        2: "2 - Logradouro",
        3: "3 - Bairro",
        4: "4 - Município"
    }

    def attempt_geocode(row):
        # Tenta os níveis em cascata: 1 (Porta) -> 2 (Logradouro) -> 3 (Bairro) -> 4 (Município)
        niveis = ['nivel_1_porta', 'nivel_2_logradouro', 'nivel_3_bairro', 'nivel_4_municipio']
        
        for precisao, col in enumerate(niveis, start=1):
            addr = row[col]
            if not addr or addr.strip() == "":
                continue
            
            loc = geocode(addr)
            if loc:
                # Retorna latitude, longitude, descrição da precisão e o endereço devolvido pelo Nominatim
                return pd.Series([loc.latitude, loc.longitude, descricoes_precisao[precisao], loc.address])
            
            # Adiciona um tempo de espera (timesleep) explícito de 1.25s antes de tentar o próximo nível
            time.sleep(1.25)
            
        # Se falhar em todos os 4 níveis
        return pd.Series([None, None, None, None])

    df[['latitude', 'longitude', 'precisao', 'location_ref']] = df.progress_apply(attempt_geocode, axis=1)

    return df

def adiciona_ao_db(db_path, df, view_name):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = f"""
        CREATE TABLE IF NOT EXISTS enderecos_georreferenciados_{view_name} (
            cnpj_completo TEXT PRIMARY KEY,
            endereco TEXT,
            latitude REAL,
            longitude REAL,
            precisao TEXT,
            location_ref TEXT
        )
    """
    cursor.execute(query)

    # Filtra os dados necessários utilizando o cnpj_completo como chave principal
    lista_tuplas = df[['cnpj_completo', 'nivel_1_porta', 'latitude', 'longitude', 'precisao', 'location_ref']].values.tolist()
    
    # Utiliza INSERT OR IGNORE para pular suavemente CNPJs já salvos
    cursor.executemany(f"INSERT OR IGNORE INTO enderecos_georreferenciados_{view_name} (cnpj_completo, endereco, latitude, longitude, precisao, location_ref) VALUES (?, ?, ?, ?, ?, ?)", lista_tuplas)
    conn.commit()
    conn.close()


db_path = r"C:\Users\Mateus Joter\Desktop\CNPJ\dados_receita.db"
view_name = "RMG_2025"


if __name__ == '__main__':
    # 1. Carrega os dados originais (que já contêm a coluna 'cnpj_completo')
    df = carregar_dados(db_path, view_name)
    
    # 2. Passa o DF pelo tratamento do Gemini antes da montagem da cascata
    df = tratar_logradouros_gemini(df)
    
    # 3. Cria as strings de níveis em cascata para todo o dataset
    df = criar_cascata_enderecos(df)
    
    # 4. Isola apenas os endereços ÚNICOS para economizar consultas na API
    df_unicos = separar_unicos(df)
    
    # 5. Faz a geolocalização somentes dos únicos com Nominatim
    df_geolocalizado = geolocalizar(df_unicos)
    
    # 6. Faz o 'merge' dos resultados geolocalizados de volta para o DataFrame completo 
    # utilizando o endereço principal (nível_1_porta) como ponte.
    # Assim, CNPJs que dividem o mesmo endereço herdam a mesma latitude/longitude tratada, sem reconsultar a API.
    df_final = df.merge(
        df_geolocalizado[['nivel_1_porta', 'latitude', 'longitude', 'precisao', 'location_ref']], 
        on='nivel_1_porta', 
        how='left'
    )
    
    # 7. Salva o dataset finalizado no banco de dados usando o 'cnpj_completo' como PRIMARY KEY
    adiciona_ao_db(db_path, df_final, view_name)