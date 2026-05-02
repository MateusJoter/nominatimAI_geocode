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

def selecionar_coluna(df):
    columns_list = df.columns.to_list()

    chosen_column = str()
    while chosen_column not in columns_list:
        input(f"Selecione a coluna a ser georeferenciada: {', '.join(columns_list)}")

        if chosen_column not in columns_list:
            print(f"""
            {'ERRO:'.ljust(7)}A coluna selecionada não está no conjunto de dados!!!
            {''.ljust(7)}Verifique se escreveu corretamente e tente de novo!!! \n
            """)
    
    return chosen_column

def separar_unicos(df, nome_coluna):
    unique_array = df[nome_coluna].unique()
    unique_df = pd.DataFrame(unique_array, columns = [nome_coluna])

    return unique_df

def geolocalizar(df, nome_coluna):
    geolocator = Nominatim(user_agent="meu_projeto_bulk_v1")

    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1, max_retries=2)

    tqdm.pandas()

    df['location'] = df[nome_coluna].progress_apply(geocode)

    df['latitude'] = df['location'].apply(lambda loc: loc.latitude if loc else None)
    df['longitude'] = df['location'].apply(lambda loc: loc.longitude if loc else None)

    return df

def adiciona_ao_db(db_path, df):
    conn = sqlite3(db_path)
    cursor = conn(cursor)

    query = """
        CREATE TABLE IF NOT EXISTS enderecos_georreferenciados (
            endereco TEXT PRIMARY KEY,
            latitude TEXT NOT NULL,
            longitude
        )
    """
    cursor.execute(query)

    lista_tuplas_enderecos_georreferenciados = list(df.itertuples(index=False, name=None))
    cursor.executemany("INSERT INTO usuarios (nome, idade, email) VALUES (?, ?, ?)", lista_tuplas_enderecos_georreferenciados)
    conn.close()


db_path = ''
view_name = ''


if __name__ == '__main__':
    df = carregar_dados(db_path, view_name)
    nome_coluna = selecionar_coluna(df)
    df_unicos = separar_unicos(df)
    df_geolocalizado = geolocalizar(df_unicos, nome_coluna)
    adiciona_ao_db(db_path, df_geolocalizado)