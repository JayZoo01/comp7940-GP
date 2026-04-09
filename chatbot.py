'''
This program requires the following modules:
- python-telegram-bot==22.5
- urllib3==2.6.2
- redis==5.0.3  <-- 新增的依赖
'''

from ChatGPT_HKBU import ChatGPT
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters
from pymongo import MongoClient
import configparser
import datetime
import logging
import certifi
import redis  # 1. 引入 redis 库
import os     # 用于获取环境变量

db = None
redis_client = None  # 全局 Redis 客户端

def init_redis():
    """initialize Redis connection and set the global redis_client variable"""
    global redis_client
    try:
        # 在 docker-compose 中，服务名就是主机名，端口默认 6379
        redis_host = os.environ.get('REDIS_HOST', 'redis')
        redis_client = redis.Redis(host=redis_host, port=6379, decode_responses=True)
        redis_client.ping() # 测试连接
        logging.info("✅ Redis connection successful!")
    except Exception as e:
        logging.error(f"❌ Redis connection failed: {e}")
        redis_client = None # 如果连不上，就置空，后续代码会自动回退到不使用缓存

def init_mongodb(config):
    """initialize MongoDB connection and set the global db variable"""
    global db
    try:
        uri = config['MONGODB']['CONNECTION_STRING']
        client = MongoClient(uri, tlsCAFile=certifi.where())
        db = client[config['MONGODB']['DATABASE_NAME']]
        
        # test the connection immediately
        client.admin.command('ping')
        logging.info("✅ MongoDB connection successful!")
    except Exception as e:
        logging.error(f"❌ MongoDB connection failed: {e}")

def get_system_config():
    """capture system configuration from MongoDB and return the prompt string"""
    try:
        config_data = db.configs.find_one({"role": "weather_assistant"})
        if config_data:
            return config_data.get('prompt')
        return "你是一个天气预报助手。"
    except Exception as e:
        logging.error(f"获取配置失败: {e}")
        return "你是一个助手。"

def log_to_db(user_id, user_name, text, response):
    """write the user query and bot response into MongoDB logs"""
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
        db.user_logs.insert_one(log_document)
        logging.info(f"日志已存入数据库")
    except Exception as e:
        logging.error(f"写入 MongoDB 日志表失败: {e}")

def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        level=logging.INFO)
    
    logging.info('INIT: Loading configuration...')
    config = configparser.ConfigParser()
    config.read('config.ini')

    # 初始化数据库和缓存
    init_mongodb(config)
    init_redis()

    logging.info('INIT: Connecting the Telegram bot...')
    app = ApplicationBuilder().token(config['TELEGRAM']['ACCESS_TOKEN']).build()

    global gpt
    gpt = ChatGPT(config)

    logging.info('INIT: Registering the message handler...')
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, callback))

    logging.info('INIT: Initialization done!')
    app.run_polling()

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global db, gpt, redis_client
    logging.info("UPDATE: " + str(update))
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name
    user_text = update.message.text

    loading_message = await update.message.reply_text('正在思考中...')

    # ==========================
    # 核心改造：引入 Redis 缓存层
    # ==========================
    
    cached_response = None
    # 1. 尝试从 Redis 读取缓存
    if redis_client:
        try:
            cached_response = redis_client.get(user_text)
        except Exception as e:
            logging.error(f"Redis get 报错: {e}")

    # 2. 如果缓存命中了！
    if cached_response:
        final_response = f"⚡ [来自 Redis 极速缓存]\n{cached_response}"
        # 记录日志并直接返回，不再调用大模型！
        log_to_db(user_id, user_name, user_text, final_response)
        await loading_message.edit_text(final_response)
        return

    # 3. 如果缓存没命中，执行原有的 GPT 调用逻辑
    system_prompt = get_system_config()
    combined_query = f"系统设定：{system_prompt}\n用户问题：{user_text}"
    response = gpt.submit(combined_query)

    # 4. 将最新拿到的结果存入 Redis，设置 1 小时（3600秒）过期
    if redis_client and not response.startswith("Error:"):
        try:
            redis_client.setex(user_text, 3600, response)
            logging.info("✅ 已将新回复写入 Redis 缓存")
        except Exception as e:
            logging.error(f"Redis set 报错: {e}")

    # 5. 存入日志并回复用户
    log_to_db(user_id, user_name, user_text, response)
    await loading_message.edit_text(response)

if __name__ == '__main__':
    main()