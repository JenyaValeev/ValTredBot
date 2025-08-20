# crypto_manager.py
from cryptography.fernet import Fernet
import os
import logging

log = logging.getLogger("bybit_bot.crypto")

class CryptoManager:
    def __init__(self):
        key = os.getenv("CRYPTO_KEY")
        if not key:
            key = Fernet.generate_key().decode()
            log.warning("CRYPTO_KEY не установлен. Сгенерирован ключ. Поместите этот ключ в CRYPTO_KEY: %s", key)
            print("Generated CRYPTO_KEY:", key)
            # Не записываем в os.environ автоматически — пользователь должен сохранить в .env
        self.key = key.encode()
        self.cipher = Fernet(self.key)

    def encrypt(self, plaintext: str) -> str:
        return self.cipher.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self.cipher.decrypt(token.encode()).decode()
