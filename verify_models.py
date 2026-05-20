from google import genai
print(str(open("gemini_apikey.txt", "r").read()))
client = genai.Client(api_key=str(open("gemini_apikey.txt", "r").read()))

try:
    print("Modelos disponíveis para sua chave:")
    print("-" * 50)
    for model in client.models.list():
        # No SDK novo, acessamos .name e .supported_methods
        print(f"Nome: {model.name}")
        
except Exception as e:
    print(f"Erro ao listar: {e}")