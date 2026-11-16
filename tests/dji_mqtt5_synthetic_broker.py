"""Small synthetic MQTT 5 TLS peer for protocol tests; not a DJI simulator."""
import json, os, socket, ssl, struct, sys
from pathlib import Path
from uuid import uuid4

def read_exact(sock, n):
    out = b''
    while len(out) < n:
        part = sock.recv(n-len(out))
        if not part: raise EOFError()
        out += part
    return out

def recv_packet(sock):
    first = read_exact(sock,1)[0]; remaining=0; multiplier=1
    while True:
        byte=read_exact(sock,1)[0]; remaining += (byte & 127)*multiplier
        if byte < 128: break
        multiplier *= 128
    if remaining > 131072: raise ValueError('oversize')
    return first, read_exact(sock,remaining)

def size(n):
    out=[]
    while True:
        b=n%128;n//=128;out.append(b | (128 if n else 0))
        if not n:return bytes(out)

def send_packet(sock,kind,body): sock.sendall(bytes([kind])+size(len(body))+body)

def mqtt_publish(sock,topic,payload,mid):
    name=topic.encode();send_packet(sock,0x32,struct.pack('!H',len(name))+name+struct.pack('!H',mid)+b'\x00'+payload)

def parse_publish(body):
    length=struct.unpack('!H',body[:2])[0];topic=body[2:2+length].decode();pos=2+length
    mid=struct.unpack('!H',body[pos:pos+2])[0];pos+=2
    assert body[pos] == 0; pos+=1
    return topic, mid, json.loads(body[pos:])

host=os.environ['DJI_TEST_HOST'];port=int(os.environ['DJI_TEST_PORT']);output=Path(os.environ['DJI_TEST_RESULT'])
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(os.environ['DJI_TEST_CERT'],os.environ['DJI_TEST_KEY'])
listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind((host,port));listener.listen(1);print('READY',flush=True)
try:
    def connect_and_subscribe():
        peer,_=listener.accept(); peer=context.wrap_socket(peer,server_side=True);peer.settimeout(20)
        kind,body=recv_packet(peer);assert kind==0x10 and b'MQTT\x05' in body;send_packet(peer,0x20,b'\x00\x00\x00')
        kind,body=recv_packet(peer);assert kind==0x82
        # Preserve RETAIN and suppress historical retained messages.
        pos=3; options=[]
        while pos<len(body):
            length=struct.unpack('!H',body[pos:pos+2])[0];pos+=2+length
            options.append(body[pos]);pos+=1
        assert options==[0x29,0x29]
        return peer,body
    sock,body=connect_and_subscribe()
    early=os.environ.get('DJI_TEST_EARLY_SUBACK')
    if early:
        tid=str(uuid4());bid=str(uuid4())
        request=json.dumps({'method':'config','tid':tid,'bid':bid,'data':{'config_type':'json','config_scope':'product'}}).encode()
        mqtt_publish(sock,'thing/product/SYNTHDOCK/requests',request,98)
        if early=='overflow':
            for extra_mid in range(100,132):
                mqtt_publish(sock,'thing/product/SYNTHDOCK/requests',request,extra_mid)
        # Nothing, including an ACK, may be emitted before SUBACK.
        if early!='overflow':
            sock.settimeout(.4)
            try: recv_packet(sock);raise AssertionError('premature response or acknowledgement')
            except socket.timeout: pass
            sock.settimeout(20)
        if early in ('early_fail','overflow'):
            if early=='early_fail': send_packet(sock,0x90,body[:2]+b'\x00\x01\x80')
            kind,_=recv_packet(sock);assert kind==0xe0  # no request ACK or credentials
            sock.close()
            # Session expiry is zero: explicitly simulate device retry, not
            # a broker guarantee of redelivery across the disconnect.
            sock,body=connect_and_subscribe()
            mqtt_publish(sock,'thing/product/SYNTHDOCK/requests',request,98)
        send_packet(sock,0x90,body[:2]+b'\x00\x01\x01')
        got_reply=got_ack=False
        while not (got_reply and got_ack):
            kind,part=recv_packet(sock)
            if kind & 0xf0==0x30:
                target,mid,reply=parse_publish(part)
                assert target=='thing/product/SYNTHDOCK/requests_reply' and reply['tid']==tid and reply['bid']==bid
                send_packet(sock,0x40,struct.pack('!H',mid));got_reply=True
            elif kind==0x40: got_ack=struct.unpack('!H',part[:2])[0]==98
    else:
        send_packet(sock,0x90,body[:2]+b'\x00\x01\x01')
    # A retained old config request must be acknowledged and discarded, not answered.
    old_topic=b'thing/product/SYNTHDOCK/requests'
    old_payload=json.dumps({'method':'config','tid':str(uuid4()),'bid':str(uuid4()),
                            'data':{'config_type':'json','config_scope':'product'}}).encode()
    send_packet(sock,0x33,struct.pack('!H',len(old_topic))+old_topic+struct.pack('!H',99)+b'\x00'+old_payload)
    kind,body=recv_packet(sock);assert kind==0x40 and struct.unpack('!H',body[:2])[0]==99
    events=[('sys/product/SYNTHDOCK/status','update_topo',{'device_secret':'synthetic-device-secret','nonce':'synthetic-nonce','sub_devices':[{'sn':'SYNTHAIR','device_secret':'synthetic-air-secret','nonce':'synthetic-air-nonce'}]}),
            ('thing/product/SYNTHDOCK/requests','config',{'config_type':'json','config_scope':'product'}),
            ('thing/product/SYNTHDOCK/requests','airport_bind_status',{'devices':[{'sn':'SYNTHDOCK'},{'sn':'SYNTHAIR'}]}),
            ('thing/product/SYNTHDOCK/requests','airport_organization_get',{'organization_id':'synthetic-org','device_binding_code':'synthetic-dock-code'}),
            ('thing/product/SYNTHDOCK/requests','airport_organization_bind',{'bind_devices':[{'sn':'SYNTHDOCK','organization_id':'synthetic-org','device_binding_code':'synthetic-dock-code'},{'sn':'SYNTHAIR','organization_id':'synthetic-org','device_binding_code':'synthetic-air-code'}]}),
            ('thing/product/SYNTHDOCK/requests','airport_bind_status',{'devices':[{'sn':'SYNTHDOCK'},{'sn':'SYNTHAIR'}]})]
    observations=[]
    for index,(topic,method,data) in enumerate(events,1):
        tid=str(uuid4());bid=str(uuid4());payload=json.dumps({'method':method,'tid':tid,'bid':bid,'gateway':'SYNTHDOCK','data':data}).encode()
        mqtt_publish(sock,topic,payload,index);reply=None;received=False
        while reply is None or not received:
            kind,body=recv_packet(sock)
            if kind & 0xf0 == 0x30:
                target,mid,reply=parse_publish(body)
                if os.environ.get('DJI_TEST_REJECT_REPLY') == '1' and method == 'config':
                    # MQTT 5 negative PUBACK: do not treat is_published as success.
                    send_packet(sock,0x40,struct.pack('!H',mid)+b'\x87\x00')
                    sock.settimeout(3);acked=False
                    try:
                        while True:
                            kind2,body2=recv_packet(sock)
                            if kind2 == 0x40 and struct.unpack('!H',body2[:2])[0]==index:acked=True;break
                            if kind2 == 0xe0:break
                    except (EOFError,TimeoutError,socket.timeout):pass
                    assert not acked
                    output.write_text(json.dumps({'rejected':True,'request_acknowledged':False}));sock.close();sys.exit(0)
                send_packet(sock,0x40,struct.pack('!H',mid))
                assert target == ('sys/product/SYNTHDOCK/status_reply' if method=='update_topo' else 'thing/product/SYNTHDOCK/requests_reply')
                assert reply['method']==method and reply['tid']==tid and reply['bid']==bid
            elif kind == 0x40: received = struct.unpack('!H',body[:2])[0]==index
            elif kind == 0xc0:send_packet(sock,0xd0,b'')
        if method=='config':assert reply['data']['app_key']=='synthetic-app-key'
        if method=='airport_bind_status':observations.append([x['is_device_bind_organization'] for x in reply['data']['output']['bind_status']])
    assert observations==[[False,False],[True,True]]
    output.write_text(json.dumps({'completed':True,'methods':len(events)}));sock.close()
finally:listener.close()
