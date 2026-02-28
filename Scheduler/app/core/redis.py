import redis.asyncio as redis

redis_client = redis.Redis(
    host="redis",
    port=6379,
    decode_responses=True
)
