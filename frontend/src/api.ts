export async function api<T>(path:string, init:RequestInit = {}):Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type','application/json');
  let response:Response;
  try { response = await fetch(`/api${path}`, {...init,headers,credentials:'same-origin'}); }
  catch { throw new Error('暂时无法连接服务，请确认后端已启动后重试。'); }
  let data:unknown;
  try { data = await response.json(); } catch { throw new Error(`服务返回了无法读取的响应（${response.status}）。`); }
  if (!response.ok) {
    const detail = (data as {detail?:unknown}).detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? '提交的信息有误，请检查选项与必填项。' : `请求未完成（${response.status}），请稍后重试。`);
  }
  return data as T;
}
export function post<T>(path:string, body:unknown = {}):Promise<T> {return api<T>(path,{method:'POST',body:JSON.stringify(body)});}
