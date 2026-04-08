'''
This program requires the following modules:
- python-telegram-bot==22.5
- urllib3==2.6.2
'''

from ChatGPT_HKBU import ChatGPT
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from pymongo import MongoClient
import configparser
import datetime
import logging
import certifi

db = None

def init_mongodb(config):
    """initialize MongoDB connection and set the global db variable"""
    global db
    try:
        uri = config['MONGODB']['CONNECTION_STRING']
        client = MongoClient(uri, tlsCAFile=certifi.where())
        db = client[config['MONGODB']['DATABASE_NAME']]
        
        
        # test the connection immediately
        client.admin.command('ping')
        print("✅ MongoDB connection successful!")
        logging.info("✅ MongoDB connection successful!")
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")
        logging.error(f"❌ MongoDB connection failed: {e}")

def get_system_config():
    """capture system configuration from MongoDB and return the prompt string"""
    try:
        # search for the config with role "weather_assistant" (you can change this as needed)
        config_data = db.configs.find_one({"role": "weather_assistant"})
        if config_data:
            return config_data.get('prompt')
        # if not found, return a default prompt
        return "你是一个天气预报助手。"
    except Exception as e:
        logging.error(f"获取配置失败: {e}")
        return "你是一个助手。"

def log_to_db(user_id, user_name, text, response):
    """write the user query and bot response into MongoDB logs collection with a structured format"""
    try:
        log_document = {
            "user_info": {
                "id": user_id,
                "name": user_name
            },
            "chat_content": {
                "user_query": text,
                "bot_response": response
            },
            "meta_data": {
                "timestamp": datetime.datetime.utcnow(), 
                "source": "Telegram",
                "model": "gpt-4o-mini"
            }
        }
        
        # 执行写入操作
        result = db.user_logs.insert_one(log_document)
        logging.info(f"日志已存入数据库，ID: {result.inserted_id}")
        
    except Exception as e:
        logging.error(f"写入 MongoDB 日志表失败: {e}")

def main():
    # Configure logging so you can see initialization and error messages
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        level=logging.INFO)
    
    # Load the configuration data from file
    logging.info('INIT: Loading configuration...')
    config = configparser.ConfigParser()
    config.read('config.ini')

    init_mongodb(config)
    # Create an Application for your bot
    logging.info('INIT: Connecting the Telegram bot...')
    app = ApplicationBuilder().token(config['TELEGRAM']['ACCESS_TOKEN']).build()


    global gpt
    gpt = ChatGPT(config)
    # Register a message handler
    logging.info('INIT: Registering the message handler...')
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, callback))

    # Start the bot
    logging.info('INIT: Initialization done!')
    app.run_polling()

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global db
    logging.info("UPDATE: " + str(update))
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name
    user_text = update.message.text

    loading_message = await update.message.reply_text('正在思考中...')

    # 1. 自动拉取最新的投喂表设定
    system_prompt = get_system_config()
    
    # 2. 将设定和用户问题组合（你可以根据 ChatGPT 类的具体实现调整传参方式）
    combined_query = f"系统设定：{system_prompt}\n用户问题：{user_text}"
    
    # 3. 发送给 GPT
    response = gpt.submit(combined_query)

    # 4. 存入日志表
    log_to_db(user_id, user_name, user_text, response)

    await loading_message.edit_text(response)

if __name__ == '__main__':
    main()
