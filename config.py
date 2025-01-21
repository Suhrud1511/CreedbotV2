import os
from dotenv import load_dotenv

# Determine which environment file to load
ENV = os.getenv('ENVIRONMENT', 'development')
env_file = '.env.production' if ENV == 'production' else '.env.development'

# Load the appropriate environment file
load_dotenv(env_file)

# Configuration class
class Config:
    MONGO_URI = os.getenv('MONGO_URI')
    
    @staticmethod
    def is_production():
        return os.getenv('ENVIRONMENT') == 'production'