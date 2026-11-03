/** A local revocation checked before any action write, never a remote result. */
export class ActionAuthorizationRevoked extends Error {
  readonly statusCode = 409;
  readonly uncertain = false;
  constructor() { super("动作请求已失效"); }
}

export function assertActionAuthorized(authorize?: () => boolean): void {
  if (authorize && !authorize()) throw new ActionAuthorizationRevoked();
}
