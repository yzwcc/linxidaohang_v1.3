const NORMAL_LIMIT = 4;
const PRIORITY_LIMIT = 5;

let active = 0;
type QueueEntry = {
  start: () => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
};

const priorityQueue: QueueEntry[] = [];
const normalQueue: QueueEntry[] = [];

function acquire(priority: boolean, queueTimeoutMs: number): Promise<void> {
  const limit = priority ? PRIORITY_LIMIT : NORMAL_LIMIT;
  if (active < limit) {
    active += 1;
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const queue = priority ? priorityQueue : normalQueue;
    const entry = {} as QueueEntry;
    entry.reject = reject;
    entry.start = () => {
      clearTimeout(entry.timer);
      active += 1;
      resolve();
    };
    entry.timer = setTimeout(() => {
      const index = queue.indexOf(entry);
      if (index >= 0) queue.splice(index, 1);
      reject(new Error("机器人请求排队超时，请稍后重试"));
    }, queueTimeoutMs);
    queue.push(entry);
  });
}

function release(): void {
  active = Math.max(0, active - 1);
  const nextPriority = priorityQueue.shift();
  if (nextPriority && active < PRIORITY_LIMIT) {
    nextPriority.start();
    return;
  }
  const nextNormal = normalQueue.shift();
  if (nextNormal && active < NORMAL_LIMIT) {
    nextNormal.start();
    return;
  }
  if (nextPriority) priorityQueue.unshift(nextPriority);
  if (nextNormal) normalQueue.unshift(nextNormal);
}

export async function withRobotTransportSlot<T>(
  task: () => Promise<T>,
  priority = false,
  queueTimeoutMs = priority ? 5000 : 2500
): Promise<T> {
  await acquire(priority, queueTimeoutMs);
  try {
    return await task();
  } finally {
    release();
  }
}
