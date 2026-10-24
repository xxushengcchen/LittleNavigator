"""只读 SDL2 方向盘按钮。后台轮询，不发送按键、不占用独占模式、不设置力反馈。"""
from __future__ import annotations
import ctypes
import threading
import time


class WheelInput:
    def __init__(self):
        self._stop=threading.Event()
        self._lock=threading.Lock()
        self._ready=threading.Event()
        self._devices=[]
        self.error=None
        self._thread=threading.Thread(target=self._loop,daemon=True,name='wheel-input')
        self._thread.start()
        self._ready.wait(2)

    def snapshot(self):
        with self._lock:
            return [{**d,'pressed':list(d['pressed'])} for d in self._devices]

    def devices(self):
        return [{k:v for k,v in d.items() if k!='pressed'} for d in self.snapshot()]

    @staticmethod
    def matches(binding,devices):
        same=[d for d in devices if d['guid']==binding.get('guid')]
        if binding.get('serial'):
            return [d for d in same if d.get('serial')==binding['serial']]
        exact=[d for d in same if binding.get('path') and d.get('path')==binding['path']]
        return exact or (same if len(same)==1 else [])

    def pressed(self,binding):
        matches=self.matches(binding,self.snapshot())
        return len(matches)==1 and binding['button'] in matches[0]['pressed']

    def capture(self,timeout_s=8,device_guid=None):
        if self.error:raise ValueError(self.error)
        if not any(d['buttons']>0 for d in self.snapshot()):raise ValueError('未检测到带按钮的方向盘或控制器，请连接设备后重试')
        initial={(d['instance'],b) for d in self.snapshot() for b in d['pressed']}
        deadline=time.monotonic()+timeout_s
        while time.monotonic()<deadline and not self._stop.is_set():
            devices=self.snapshot()
            down={(d['instance'],b) for d in devices for b in d['pressed']}
            initial.intersection_update(down)
            candidates=[(d,b) for d in devices if not device_guid or d['guid']==device_guid
                        for b in d['pressed'] if (d['instance'],b) not in initial]
            if len(candidates)>1:raise ValueError('请只按一个方向盘按钮')
            if candidates:
                d,b=candidates[0]
                binding={k:d.get(k,'') for k in ('guid','name','serial','path')}
                binding.update(type='wheel',button=b)
                return {'binding':binding,'name':binding_label(binding)}
            self._stop.wait(.01)
        raise ValueError('超时未检测到方向盘按钮')

    def _loop(self):
        handles={}
        initialized=False
        try:
            import sdl2 as sdl
            sdl.SDL_SetHint(b'SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS',b'1')
            if sdl.SDL_InitSubSystem(sdl.SDL_INIT_JOYSTICK)!=0:
                raise RuntimeError(sdl.SDL_GetError().decode('utf-8','replace'))
            initialized=True
            # 使用状态轮询，避免游戏中大量轴事件堆积在 SDL 事件队列。
            sdl.SDL_JoystickEventState(sdl.SDL_IGNORE)
            refresh=0
            def string(value):return value.decode('utf-8','replace') if value else ''
            while not self._stop.is_set():
                now=time.monotonic()
                if now>=refresh:
                    present=set()
                    for index in range(sdl.SDL_NumJoysticks()):
                        iid=sdl.SDL_JoystickGetDeviceInstanceID(index)
                        present.add(iid)
                        if iid not in handles:
                            handle=sdl.SDL_JoystickOpen(index)
                            if not handle:continue
                            buf=ctypes.create_string_buffer(33)
                            sdl.SDL_JoystickGetGUIDString(sdl.SDL_JoystickGetGUID(handle),buf,33)
                            meta={'instance':iid,'guid':buf.value.decode(),'name':string(sdl.SDL_JoystickName(handle)),
                                  'serial':string(sdl.SDL_JoystickGetSerial(handle)) if hasattr(sdl,'SDL_JoystickGetSerial') else '',
                                  'path':string(sdl.SDL_JoystickPath(handle)) if hasattr(sdl,'SDL_JoystickPath') else '',
                                  'buttons':sdl.SDL_JoystickNumButtons(handle)}
                            handles[iid]=(handle,meta)
                    for iid in set(handles)-present:
                        handle,_=handles.pop(iid);sdl.SDL_JoystickClose(handle)
                    refresh=now+1
                sdl.SDL_JoystickUpdate()
                devices=[]
                for handle,meta in handles.values():
                    if sdl.SDL_JoystickGetAttached(handle):
                        devices.append({**meta,'pressed':[b for b in range(meta['buttons']) if sdl.SDL_JoystickGetButton(handle,b)]})
                with self._lock:self._devices=devices
                self._ready.set()
                self._stop.wait(.01)
        except Exception as exc:
            self.error=f'方向盘输入不可用：{exc}'
            self._ready.set()
        finally:
            if initialized:
                for handle,_ in handles.values():sdl.SDL_JoystickClose(handle)
                sdl.SDL_QuitSubSystem(sdl.SDL_INIT_JOYSTICK)
            with self._lock:self._devices=[]

    def close(self):
        self._stop.set();self._thread.join(2)


_instance=None
_instance_lock=threading.Lock()
def get_input():
    global _instance
    with _instance_lock:
        if _instance is None or _instance._stop.is_set():_instance=WheelInput()
        return _instance

def close_input():
    global _instance
    with _instance_lock:
        if _instance is not None:_instance.close();_instance=None

def binding_label(binding,vk=None):
    if binding and binding.get('type')=='wheel':
        return f"{binding.get('name','方向盘')} · 按钮 {binding['button']+1}"
    return f'键盘 0x{vk:02X}' if vk is not None else '键盘'
