import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging():
    base = os.path.join(os.path.expanduser("~"), ".wifi_doctor_tools")
    os.makedirs(base, exist_ok=True)
    log_file = os.path.join(base, "wifi_doctor.log")
    
    logger = logging.getLogger("wifi_doctor")
    logger.setLevel(logging.DEBUG)
    
    # Rotating file handler (5MB per file, 3 backups)
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()
