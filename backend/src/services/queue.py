import os
from redis import Redis

def client():
    return Redis.from_url(os.environ['REDIS_URL'])

def enqueue(run_id):
    client().rpush('analysis-queue',run_id)
