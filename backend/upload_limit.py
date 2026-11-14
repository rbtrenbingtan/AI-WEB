"""Bound multipart input before Starlette parses or spools uploaded files."""
from starlette.responses import JSONResponse


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes=10 * 1024 * 1024 + 64 * 1024):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') != '/api/documents':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            declared = self.max_bytes + 1
        async def reject():
            await JSONResponse({'detail': '上传请求超过 10 MB 文件限制'}, status_code=413)(scope, receive, send)
        if declared > self.max_bytes:
            return await reject()
        messages, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > self.max_bytes:
                return await reject()
            messages.append(message)
            if not message.get('more_body', False):
                break
        iterator = iter(messages)
        async def bounded_receive():
            return next(iterator, None) or await receive()
        await self.app(scope, bounded_receive, send)
