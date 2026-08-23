export interface RpcTransport {
  request<TResult>(method: string, params?: object): Promise<TResult>;
  notifications(): AsyncIterable<unknown>;
  close(): Promise<void>;
}
