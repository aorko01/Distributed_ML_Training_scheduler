import asyncio
from collections import Counter


class CapacityError(Exception):
    pass


class Capacity:
    def __init__(self, settings):
        self.settings = settings
        self.unauthenticated = 0
        self.active = {}
        self.users = Counter()
        self.lock = asyncio.Lock()

    async def begin_auth(self):
        async with self.lock:
            if self.unauthenticated >= self.settings.unauthenticated_connections:
                raise CapacityError()
            self.unauthenticated += 1

    async def end_auth(self):
        async with self.lock:
            self.unauthenticated -= 1

    async def reserve(self, session, user, task):
        async with self.lock:
            if (session in self.active or len(self.active) >= self.settings.total_connections
                    or self.users[user] >= self.settings.per_user_connections):
                raise CapacityError()
            self.active[session] = (user, task)
            self.users[user] += 1

    async def release(self, session):
        async with self.lock:
            value = self.active.pop(session, None)
            if value:
                self.users[value[0]] -= 1
                if not self.users[value[0]]:
                    del self.users[value[0]]

    async def shutdown(self):
        tasks = [task for _, task in self.active.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
