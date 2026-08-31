# -*- coding: utf-8 -*-
"""SO101 robot arm web control - slider control + trajectory record & playback.

Drag sliders to move all six joints in real time, or record a trajectory
(50 Hz of the arm's true position) and play it back on demand. No ROS,
no web framework - just one Python file on top of LeRobot.
"""
import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

PORT = '/dev/ttyACM0'
ARM_ID = 'so_follower'
TRAJ_DIR = os.path.expanduser('~/.so101_arm/trajs')
os.makedirs(TRAJ_DIR, exist_ok=True)

# Standby posture (arm resting on the table)
REST_POSE = {
    'shoulder_pan': 0, 'shoulder_lift': -66, 'elbow_flex': 98,
    'wrist_flex': 77, 'wrist_roll': 0, 'gripper': 1,
}

robot = SO101Follower(SO101FollowerConfig(port=PORT, id=ARM_ID))
robot.connect()
print('Robot arm connected')

JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']
target = dict(REST_POSE)
lock = threading.Lock()

# Record / playback state
mode = 'idle'  # idle | record | play
record_buf = []
rec_t0 = 0.0
play_frames = []
play_t0 = 0.0


def interp(frames, t):
    """Interpolate joint positions at time t within a frame list.

    Returns None when t is past the end (playback finished).
    """
    if t <= frames[0]['t']:
        return dict(frames[0]['pos'])
    if t >= frames[-1]['t']:
        return None
    for a, b in zip(frames, frames[1:]):
        if a['t'] <= t < b['t']:
            k = (t - a['t']) / (b['t'] - a['t'])
            return {j: a['pos'][j] + (b['pos'][j] - a['pos'][j]) * k for j in JOINTS}
    return dict(frames[-1]['pos'])


def motion_loop():
    global mode, play_t0
    current = dict(REST_POSE)
    while True:
        if mode == 'play' and play_frames:
            pose = interp(play_frames, time.time() - play_t0)
            if pose is None:  # playback finished
                mode = 'idle'
                with lock:
                    target = dict(current)
            else:
                current = pose  # drive directly from trajectory, no extra smoothing
        else:
            with lock:
                goal = dict(target)
            for j in JOINTS:
                diff = goal[j] - current[j]
                step = max(0.5, abs(diff) * 0.15)  # smoothing factor
                if abs(diff) > 0.1:
                    current[j] += step if diff > 0 else -step
                else:
                    current[j] = goal[j]
            if mode == 'record':  # sample the arm's true position at 50 Hz
                record_buf.append({
                    't': round(time.time() - rec_t0, 3),
                    'pos': {j: round(current[j], 1) for j in JOINTS},
                })
        try:
            robot.send_action({f'{j}.pos': current[j] for j in JOINTS})
        except Exception as e:
            print('send failed:', e)
        time.sleep(0.02)


threading.Thread(target=motion_loop, daemon=True).start()

HTML = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SO101 Robot Arm Control</title>
<style>
body{font-family:sans-serif;background:#1e1e2e;color:#cdd6f4;max-width:640px;margin:30px auto;padding:0 20px}
h1{font-size:1.3em;text-align:center}
.row{margin:14px 0}
label{display:inline-block;width:130px}
input[type=range]{width:380px;vertical-align:middle}
.val{display:inline-block;width:70px;text-align:right;font-family:monospace}
button{padding:8px 20px;margin:5px;border:none;border-radius:6px;cursor:pointer}
.go{background:#a6e3a1}.stop{background:#f38ba8}
.rec{background:#f9e2af}.play{background:#89b4fa}
#status{text-align:center;font-family:monospace;margin:10px}
select,input[type=text]{background:#313244;color:#cdd6f4;border:none;border-radius:6px;padding:6px}
</style></head><body>
<h1>SO101 Robot Arm Control</h1>
<div id="sliders"></div>
<center>
<button class="go" onclick="sendPreset()">Rest Pose</button>
<button class="stop" onclick="stopAll()">Zero All</button>
<p>
<input type="text" id="trajname" placeholder="name" size="8">
<button class="rec" id="recbtn" onclick="toggleRec()">Record</button>
<button class="play" onclick="playSel()">Play</button>
</p>
<select id="trajlist" size="5" style="width:300px"></select>
<div id="status"></div>
</center>
<script>
const joints=['shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper'];
const ranges={'shoulder_pan':[-160,160],'shoulder_lift':[-100,100],'elbow_flex':[-100,100],
'wrist_flex':[-100,100],'wrist_roll':[-160,160],'gripper':[0,100]};
const restPose={'shoulder_pan':0,'shoulder_lift':-66,'elbow_flex':98,'wrist_flex':77,'wrist_roll':0,'gripper':1};
const s=document.getElementById('sliders');
joints.forEach(j=>{
 const[r0,r1]=ranges[j];
 const d=document.createElement('div');d.className='row';
 d.innerHTML=`<label>${j}</label><input type="range" min="${r0}" max="${r1}" value="${restPose[j]}" step="1" id="${j}"><span class="val" id="v_${j}">${restPose[j]}</span>`;
 s.appendChild(d);
 d.querySelector('input').addEventListener('input',e=>{
 document.getElementById('v_'+j).textContent=e.target.value;
 fetch('/move',{method:'POST',body:JSON.stringify({joint:j,value:parseFloat(e.target.value)})});
 });
});
function sendPreset(){joints.forEach(j=>{const el=document.getElementById(j);
 el.value=restPose[j];el.dispatchEvent(new Event('input'));});}
function stopAll(){joints.forEach(j=>{const el=document.getElementById(j);
 el.value=0;el.dispatchEvent(new Event('input'));});}
function setStatus(t){document.getElementById('status').textContent=t;}
async function refreshList(){
 const r=await fetch('/trajs');const list=await r.json();
 const sel=document.getElementById('trajlist');sel.innerHTML='';
 list.forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;sel.appendChild(o);});
}
let recording=false;
async function toggleRec(){
 const btn=document.getElementById('recbtn');
 if(!recording){
 const name=document.getElementById('trajname').value||('traj_'+Date.now());
 await fetch('/rec',{method:'POST',body:JSON.stringify({action:'start',name})});
 recording=true;btn.textContent='Stop & Save';setStatus('Recording: '+name);
 }else{
 const r=await fetch('/rec',{method:'POST',body:JSON.stringify({action:'stop'})});
 const d=await r.json();
 recording=false;btn.textContent='Record';
 setStatus('Saved '+d.name+' ('+d.frames+' frames / '+d.seconds+'s)');refreshList();
 }
}
async function playSel(){
 const sel=document.getElementById('trajlist');
 if(!sel.value){setStatus('Select a trajectory first');return;}
 await fetch('/play',{method:'POST',body:JSON.stringify({name:sel.value})});
 setStatus('Playing: '+sel.value);
}
refreshList();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/trajs':
            files = sorted(f[:-5] for f in os.listdir(TRAJ_DIR) if f.endswith('.json'))
            self._json(files)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode())

    def do_POST(self):
        global mode, rec_t0, record_buf, play_frames, play_t0
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        data = json.loads(raw) if raw else {}
        if self.path == '/move':
            with lock:
                target[data['joint']] = float(data['value'])
            self.send_response(200)
            self.end_headers()
        elif self.path == '/rec':
            if data.get('action') == 'start':
                record_buf = []
                rec_t0 = time.time()
                mode = 'record'
                self._json({'ok': True})
            else:
                mode = 'idle'
                name = data.get('name') or f'traj_{int(time.time())}'
                if record_buf:
                    with open(os.path.join(TRAJ_DIR, name + '.json'), 'w') as f:
                        json.dump(record_buf, f)
                    secs = record_buf[-1]['t']
                    frames = len(record_buf)
                    print(f'Saved {name}: {frames} frames / {secs:.1f}s')
                else:  # stopped before any sample was taken
                    secs, frames = 0.0, 0
                    print('Nothing recorded (stopped too early)')
                self._json({'ok': True, 'name': name,
                            'frames': frames, 'seconds': round(secs, 1)})
        elif self.path == '/play':
            path = os.path.join(TRAJ_DIR, data['name'] + '.json')
            if not os.path.exists(path):
                self._json({'ok': False, 'error': 'trajectory not found'})
                return
            with open(path) as f:
                raw = json.load(f)
            with lock:
                cur = {j: target[j] for j in JOINTS}
            # 1 s lead-in: smooth transition from current pose to trajectory start
            t_off = raw[0]['t'] + 1.0
            play_frames = [{'t': 0.0, 'pos': cur}] + \
                [{'t': f['t'] + t_off, 'pos': f['pos']} for f in raw]
            play_t0 = time.time()
            mode = 'play'
            self._json({'ok': True})
        else:
            self.send_response(404)
            self.end_headers()


print('Open http://localhost:8000 in your browser to control the arm')
try:
    HTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
except KeyboardInterrupt:
    pass
robot.disconnect()
print('Disconnected')
