# SmartCV Settings Constants
# Add these to your settings.py file

# Embedding Configuration
EMBEDDING_DIMENSIONS = 768  # Gemini text-embedding-004 uses 768 dimensions
EMBEDDING_MODEL = 'models/text-embedding-004'

# LLM Configuration
GAP_ANALYSIS_MODEL = 'gemini-1.5-flash'
CHATBOT_MODEL = 'gemini-1.5-flash'

# Input Validation
MAX_CHATBOT_MESSAGE_LENGTH = 5000
MAX_CV_FILE_SIZE_MB = 10
