import sqlite3
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from tqdm import tqdm
import os
import re
import platform

def limpar_terminal():
    if platform.system() == 'Windows':
        os.system('cls')
    else:
        os.system('clear')

def carregar_dados(db_path, view_name):
    conn = sqlite3.connect(db_path)
    query = f"SELECT * FROM {view_name}"

    df = pd.read_sql(query, conn)

    conn.close()

    return df

def selecionar_coluna(df):
    columns_list = df.columns.to_list()

    chosen_column = str()
    while chosen_column not in columns_list:
        chosen_column = input(f"Selecione a coluna a ser georeferenciada: {', '.join(columns_list)}\nDigite: \n")

        if chosen_column not in columns_list:
            print(f"""
            {'ERRO:'.ljust(7)}A coluna selecionada não está no conjunto de dados!!!
            {''.ljust(7)}Verifique se escreveu corretamente e tente de novo!!!
            """)

    limpar_terminal()
    
    return chosen_column

def separar_unicos(df, nome_coluna):
    unique_array = df[nome_coluna].unique()
    unique_df = pd.DataFrame(unique_array, columns = [nome_coluna])

    return unique_df

def normalizar_endereco_regex(endereco):
    """
    Limpa o endereço removendo complementos comuns em CNPJs e estruturando os dados.
    Esta função substitui a libpostal para garantir portabilidade no Windows.
    """
    if not endereco or pd.isna(endereco):
        return None

    # Converte para string e maiúsculas
    txt = str(endereco).upper()

    # 1. Lista de ruídos comuns em cadastros de CNPJ (Complementos que travam a API)
    padroes_ruido = [
        r'\bSALA\s*[A-Z0-9-]*', r'\bLOJA\s*[A-Z0-9-]*', r'\bAPTO\s*\d*', 
        r'\bAPARTAMENTO\s*\d*', r'\bBLOCO\s*[A-Z0-9]*', r'\bFUNDOS\b', 
        r'\bTERREO\b', r'\bS/\d+', r'\bKM\s*\d+', r'\bGALPAO\s*[A-Z0-9]*',
        r'\bCONJUNTO\s*[A-Z0-9]*', r'\bPAVIMENTO\s*\d*', r'\bANDAR\s*\d*'
    ]
    
    for padrao in padroes_ruido:
        txt = re.sub(padrao, '', txt)

    # 2. Tentar quebrar por vírgulas para identificar componentes
    partes = [p.strip() for p in txt.split(',')]
    
    info = {
        'rua': partes[0] if len(partes) > 0 else "",
        'numero': "",
        'bairro': "",
        'cidade_uf': ""
    }

    if len(partes) > 1:
        # Tenta identificar o número (se for dígito ou S/N)
        if re.match(r'^\d+$|^S/N$|^SN$', partes[1]):
            info['numero'] = partes[1]
            if len(partes) > 2:
                info['bairro'] = partes[2]
            info['cidade_uf'] = partes[-1]
        else:
            # Caso contrário, assume que a última parte é a Cidade/UF
            info['cidade_uf'] = partes[-1]
            info['bairro'] = partes[1] if len(partes) > 2 else ""
            
    return info

def geolocalizar(df, nome_coluna):
    """
    Geolocaliza o DataFrame usando busca em cascata e gera coluna de precisão.
    """
    geolocator = Nominatim(user_agent="geocoder_cnpj_cascata_v2", timeout=15)
    
    # RateLimiter para respeitar 1 requisição por segundo (política do Nominatim)
    geocode_limitado = RateLimiter(geolocator.geocode, min_delay_seconds=1.5, max_retries=3)

    def resolver_em_cascata(endereco_bruto):
        dados = normalizar_endereco_regex(endereco_bruto)
        if not dados:
            return None, None, None, "Vazio"

        rua, num, bairro, cidade = dados['rua'], dados['numero'], dados['bairro'], dados['cidade_uf']

        # Estratégia de Cascata: tenta do mais específico para o mais genérico
        tentativas = [
            (f"{rua}, {num}, {bairro}, {cidade}", "Alta (Porta)"),
            (f"{rua}, {num}, {cidade}", "Média (Logradouro/Num)"),
            (f"{rua}, {cidade}", "Rua (Centro)"),
            (f"{cidade}", "Baixa (Cidade)")
        ]

        for query, grau_precisao in tentativas:
            # Limpeza de strings resultantes (remove vírgulas vazias)
            query_query = re.sub(r',\s*,', ',', query).strip(', ')
            
            try:
                location = geocode_limitado(query_query)
                if location:
                    return str(location), location.latitude, location.longitude, grau_precisao
            except Exception:
                continue
        
        return None, None, None, "Não Encontrado"

    print(f"Iniciando geocodificação de {len(df)} registros...")
    tqdm.pandas(desc="Progresso")

    # Armazena o endereço original para ser usado como chave no banco
    df['_endereco_original_busca'] = df[nome_coluna]

    resultados = df[nome_coluna].progress_apply(resolver_em_cascata)
    
    # Desempacota os resultados em novas colunas
    df[['location', 'latitude', 'longitude', 'precisao']] = pd.DataFrame(
        resultados.tolist(), index=df.index
    )

    return df

def adiciona_ao_db(db_path, df):
    """
    Persiste os dados no SQLite usando a lógica original de INSERT OR IGNORE.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = """
        CREATE TABLE IF NOT EXISTS enderecos_georreferenciados (
            endereco TEXT PRIMARY KEY,
            location TEXT,
            latitude TEXT,
            longitude TEXT,
            precisao TEXT
        )
    """
    cursor.execute(query)

    # NaN para None (NULL no SQL)
    df_preparado = df.where(pd.notnull(df), None)
    
    lista_final = []
    for _, row in df_preparado.iterrows():
        # Usa a coluna de busca original como chave
        chave_endereco = row.get('_endereco_original_busca', row.iloc[0])
        
        linha = (
            str(chave_endereco),
            str(row['location']) if row['location'] else None,
            str(row['latitude']) if row['latitude'] else None,
            str(row['longitude']) if row['longitude'] else None,
            str(row['precisao'])
        )
        lista_final.append(linha)

    cursor.executemany(
        """INSERT OR IGNORE INTO enderecos_georreferenciados 
           (endereco, location, latitude, longitude, precisao) 
           VALUES (?, ?, ?, ?, ?)""", 
        lista_final
    )

    conn.commit()
    conn.close()
    print(f"Banco de dados '{db_path}' atualizado.")


db_path = r"C:\Users\Mateus Joter\Desktop\CNPJ\dados_receita.db"
view_names = ['DF_2025_tratado', 'RMF_2025_tratado', 'RMG_2025_tratado', 'RMSP_2025_tratado', 'RMRJ_2025_tratado']


if __name__ == '__main__':
    df = carregar_dados(db_path, view_names[0])
    nome_coluna = selecionar_coluna(df)
    df_unicos = separar_unicos(df, nome_coluna)
    df_geolocalizado = geolocalizar(df_unicos, nome_coluna)
    adiciona_ao_db(db_path, df_geolocalizado)