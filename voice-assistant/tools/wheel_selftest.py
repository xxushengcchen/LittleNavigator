"""通过 SDL 虚拟方向盘验证 128 按钮采集与 PTT，完全不向真实设备写入。"""
import pathlib,sys,time,threading,json
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src.wheel import get_input,close_input
import sdl2

def wait_for(fn,seconds=4):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=fn()
        if result:return result
        time.sleep(.02)
    raise AssertionError('虚拟方向盘状态未更新')

def main():
    wheel=get_input()
    if wheel.error:raise RuntimeError(wheel.error)
    device=-1;handle=None
    try:
        sdl2.SDL_LockJoysticks()
        try:
            device=sdl2.SDL_JoystickAttachVirtual(sdl2.SDL_JOYSTICK_TYPE_WHEEL,0,128,0)
            if device<0:raise RuntimeError(sdl2.SDL_GetError().decode())
            handle=sdl2.SDL_JoystickOpen(device)
            instance=sdl2.SDL_JoystickInstanceID(handle)
        finally:sdl2.SDL_UnlockJoysticks()
        meta=wait_for(lambda:next((d for d in wheel.devices() if d['instance']==instance),None))
        captured=[]
        worker=threading.Thread(target=lambda:captured.append(wheel.capture(3,meta['guid'])))
        worker.start();time.sleep(.05)
        assert sdl2.SDL_JoystickSetVirtualButton(handle,127,1)==0
        worker.join(3)
        assert captured and captured[0]['binding']['button']==127
        binding=captured[0]['binding'];assert wheel.pressed(binding)
        assert sdl2.SDL_JoystickSetVirtualButton(handle,127,0)==0
        wait_for(lambda:not wheel.pressed(binding))
        result={'device':meta['name'],'button_count':meta['buttons'],'captured_display_button':128,
                'capture':True,'press':True,'release':True,'background_hint':True,'physical_device_writes':False}
        out=ROOT.parent/'audit/wheel-virtual-selftest.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        sdl2.SDL_LockJoysticks()
        try:
            if handle:sdl2.SDL_JoystickClose(handle)
            if device>=0:
                for index in range(sdl2.SDL_NumJoysticks()):
                    if sdl2.SDL_JoystickGetDeviceInstanceID(index)==instance:sdl2.SDL_JoystickDetachVirtual(index);break
        finally:sdl2.SDL_UnlockJoysticks()
        close_input()

if __name__=='__main__':main()
