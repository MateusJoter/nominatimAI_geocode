import sqlite3
import pandas as pd
import re
import os
from geopy.geocoders import LocationIQ
from geopy.extra.rate_limiter import RateLimiter
from tqdm import tqdm

def obter_api_key():
    """Lê a chave de API do arquivo local."""
    try:
        with open("apikey_lociq.txt", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        print("Erro: Arquivo 'apikey_lociq.txt' não encontrado.")
        exit()

def carregar_dados(db_path, view_name):
    """Carrega os dados da View do SQLite."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {view_name}", conn)
    conn.close()
    return df

def limpar_logradouro_extenso(texto):
    """
    Limpa ruídos complexos de endereços brasileiros (CNPJ/Receita).
    Remove termos que não contribuem para a geolocalização espacial.
    """
    if not texto: return ""
    txt = str(texto).upper()
    
    # 1. Padrões de corte: Remove tudo o que vem após estes termos (geralmente complementos)
    cortes = [r'\bLOJA\b', r'\bSALA\b', r'\bBOX\b', r'\bSTAND\b', r'\bPAVILHAO\b', r'\bARMAZEM\b', r'\bAPTO\b']
    for c in cortes:
        txt = re.split(c, txt)[0]

    # 2. Remoção de termos específicos de ruído (Lotes, Glebas, KM, etc)
    padroes_ruido = [
        r'\bLOTE\s*[A-Z0-9/]*', r'\bLOTES\s*[A-Z0-9/]*', r'\bLT\s*[A-Z0-9/]*', r'\bLTS\s*[A-Z0-9/]*',
        r'\bAREA\s*ESPECIAL\s*\d*', r'\bMODULO\s*\d*', r'\bGLEBA\s*\d*', r'\bSUBDIVISAO\s*[A-Z0-9]*',
        r'\bPAVMTO\d*.*', r'\bPARTE\b.*', r'\bKM\s*[\d,.]*', r'\bS/N\b', r'\bSN\b',
        r'\bCONJUNTO\s*[A-Z0-9]*', r'\bCJ\s*[A-Z0-9]*', r'\bCHACARA\s*\d*', r'\bGLEBA\s*\d*'
    ]
    
    for p in padroes_ruido:
        txt = re.sub(p, '', txt)
    
    # Limpeza de espaços extras e caracteres residuais
    txt = re.sub(r'[,.-]', ' ', txt)
    return re.sub(r'\s+', ' ', txt).strip()

def geolocalizar_base(df, nome_coluna, api_key):
    """Geolocaliza usando o LocationIQ com sistema de cascata (fallback)."""
    geolocator = LocationIQ(api_key=api_key, user_agent="cnpj_geocoder_v2")
    # 0.6s de intervalo para respeitar o limite de 2 requisições por segundo
    geocode_limitado = RateLimiter(geolocator.geocode, min_delay_seconds=0.6, max_retries=3)

    def resolver(row):
        log_limpo = limpar_logradouro_extenso(row[nome_coluna])
        cep = str(row.get('cep', '')).replace('.0', '').strip()
        cidade = row.get('municipio', '')
        uf = row.get('uf', '')
        bairro = row.get('bairro', '')

        # Estratégia de Cascata para máxima qualidade
        tentativas = [
            (f"{log_limpo}, {cidade} - {uf}, {cep}, Brazil", "Alta (Logradouro+CEP)"),
            (f"{log_limpo}, {bairro}, {cidade}, Brazil", "Média (Logradouro+Bairro)"),
            (f"{cep}, Brazil", "Aproximada (CEP)"),
            (f"{cidade} - {uf}, Brazil", "Baixa (Cidade)")
        ]

        for query, grau in tentativas:
            if not query or len(query) < 10: continue # Evita queries vazias ou muito curtas
            try:
                location = geocode_limitado(query)
                if location:
                    return str(location), location.latitude, location.longitude, grau
            except:
                continue
        return None, None, None, "Não Encontrado"

    print(f"Iniciando geocodificação de {len(df)} registros únicos...")
    tqdm.pandas(desc="Progresso")
    
    resultados = df.progress_apply(resolver, axis=1)
    df[['location', 'latitude', 'longitude', 'precisao']] = pd.DataFrame(resultados.tolist(), index=df.index)
    return df

def salvar_no_db(db_path, df, nome_coluna_original):
    """Persiste os resultados no SQLite para evitar re-processamento."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS enderecos_georreferenciados (
            endereco TEXT PRIMARY KEY,
            location TEXT,
            latitude TEXT,
            longitude TEXT,
            precisao TEXT
        )
    """)

    dados_finais = []
    for _, r in df.iterrows():
        dados_finais.append((
            str(r[nome_coluna_original]),
            r['location'],
            str(r['latitude']) if r['latitude'] else None,
            str(r['longitude']) if r['longitude'] else None,
            r['precisao']
        ))

    cursor.executemany(
        "INSERT OR IGNORE INTO enderecos_georreferenciados VALUES (?, ?, ?, ?, ?)", 
        dados_finais
    )
    conn.commit()
    conn.close()

if __name__ == '__main__':
    # Configurações de Caminho e Chave
    db_path = r"C:\Users\Mateus Joter\Desktop\CNPJ\dados_receita.db"
    view_alvo = 'DF_2025_tratado' # Mude para as outras views conforme necessário
    api_key = obter_api_key()

    # 1. Carregar dados
    df_raw = carregar_dados(db_path, view_alvo)
    
    # 2. Filtrar apenas o que é único para não gastar API repetidamente
    coluna_logradouro = 'logradouro' 
    df_unicos = df_raw.drop_duplicates(subset=[coluna_logradouro, 'cep']).copy()
    
    # 3. Executar Geolocalização
    df_geo = geolocalizar_base(df_unicos, coluna_logradouro, api_key)
    
    # 4. Salvar resultados
    salvar_no_db(db_path, df_geo, coluna_logradouro)
    
    print(f"\nSucesso! Os dados georreferenciados foram salvos em 'enderecos_georreferenciados'.")