# Silero Team MIT aggregation adaptation; fixed 16k/60s candidate intervals.
import math

def speech_segments(probs,length,threshold=.5):
 """Silero Team MIT 6.2.3 default hysteresis/padding, unrounded sample coordinates."""
 if not 0<length<=960000 or len(probs)!=math.ceil(length/512) or not .1<=threshold<=.9 or any(not math.isfinite(p) or not 0<=p<=1 for p in probs):raise ValueError('语音活动分数、长度或阈值不一致')
 segments=[];active=False;start=0;temporary_end=0;negative=max(threshold-.15,.01)
 for i,prob in enumerate(probs):
  sample=i*512
  if prob>=threshold and temporary_end:temporary_end=0
  if prob>=threshold and not active:active=True;start=sample;continue
  if prob<negative and active:
   if not temporary_end:temporary_end=sample
   if sample-temporary_end<1600:continue
   if temporary_end-start>4000:segments.append({'start':start,'end':temporary_end})
   active=False;temporary_end=0
 if active and length-start>4000:segments.append({'start':start,'end':length})
 for i,segment in enumerate(segments):
  if i==0:segment['start']=max(0,segment['start']-480)
  if i<len(segments)-1:
   gap=segments[i+1]['start']-segment['end']
   if gap<960:segment['end']+=int(gap//2);segments[i+1]['start']=max(0,segments[i+1]['start']-int(gap//2))
   else:segment['end']=min(length,segment['end']+480);segments[i+1]['start']=max(0,segments[i+1]['start']-480)
  else:segment['end']=min(length,segment['end']+480)
 return segments
