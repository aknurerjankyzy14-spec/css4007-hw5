from pathlib import Path
from dotenv import dotenv_values

folder = Path(__file__).resolve().parent
env_file = folder / ".env"
settings = dotenv_values(env_file)

print("Файл .env существует:", env_file.is_file())
print("Ключ заполнен:", bool(settings.get("OPENAI_API_KEY")))
print("Модель заполнена:", bool(settings.get("MODEL_NAME")))

print(
    "Файлы с env в названии:",
    [p.name for p in folder.iterdir() if "env" in p.name.lower()],
)