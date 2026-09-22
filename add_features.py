from pathlib import Path
p=Path('/mnt/data/uc_e2ee/uc_work')
app=p/'app.py'
s=app.read_text()
needle='    conn.execute("""\n        CREATE TABLE IF NOT EXISTS message_reports ('
idx=s.index(needle)
# insert tables before message_reports
insert='''    conn.execute("""\n        CREATE TABLE IF NOT EXISTS chat_preferences (\n            user_id INTEGER NOT NULL,\n            peer_id INTEGER NOT NULL,\n            theme TEXT NOT NULL DEFAULT 'default',\n            wallpaper TEXT NOT NULL DEFAULT 'none',\n            disappearing_seconds INTEGER NOT NULL DEFAULT 0,\n            updated_at TEXT NOT NULL,\n            PRIMARY KEY (user_id, peer_id),\n            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,\n            FOREIGN KEY (peer_id) REFERENCES users(id) ON DELETE CASCADE\n        )\n    """)\n\n    conn.execute("""\n        CREATE TABLE IF NOT EXISTS user_devices (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            user_id INTEGER NOT NULL,\n            device_id TEXT NOT NULL UNIQUE,\n            device_name TEXT NOT NULL DEFAULT 'Browser',\n            identity_public_key TEXT NOT NULL,\n            signed_prekey TEXT DEFAULT NULL,\n            one_time_prekey TEXT DEFAULT NULL,\n            created_at TEXT NOT NULL,\n            last_seen_at TEXT NOT NULL,\n            revoked INTEGER NOT NULL DEFAULT 0,\n            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n        )\n    """)\n\n    conn.execute("""\n        CREATE TABLE IF NOT EXISTS encrypted_key_backups (\n            user_id INTEGER PRIMARY KEY,\n            version INTEGER NOT NULL DEFAULT 1,\n            salt TEXT NOT NULL,\n            iv TEXT NOT NULL,\n            ciphertext TEXT NOT NULL,\n            created_at TEXT NOT NULL,\n            updated_at TEXT NOT NULL,\n            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n        )\n    """)\n\n'''
s=s[:idx]+insert+s[idx:]
# Add routes before private messaging marker
marker='# ---------------------------------------------------------\n# Private messaging\n# ---------------------------------------------------------'
routes=r'''# ---------------------------------------------------------
# Appearance, chat preferences and cryptographic account tools
# ---------------------------------------------------------

@app.route('/settings/appearance', methods=['GET', 'POST'])
def appearance_settings():
    if not user_required(): return redirect(url_for('user_login'))
    uid=session['user_id']
    conn=get_db_connection()
    if request.method=='POST':
        require_csrf()
        theme=request.form.get('theme','light')
        if theme not in {'light','dark','system'}: theme='light'
        session['theme']=theme
        conn.close()
        flash('Appearance updated.', 'success')
        return redirect(url_for('appearance_settings'))
    conn.close()
    return render_template('appearance_settings.html', theme=session.get('theme','light'))

@app.route('/api/chat/<int:user_id>/preferences', methods=['GET','POST'])
def chat_preferences(user_id):
    if not user_required(): return jsonify({'error':'login_required'}),401
    if user_id==session['user_id']: return jsonify({'error':'invalid_peer'}),400
    conn=get_db_connection(); now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if request.method=='POST':
        require_csrf(); data=request.get_json(silent=True) or {}
        theme=data.get('theme','default'); wallpaper=data.get('wallpaper','none')
        try: disappear=max(0,min(int(data.get('disappearing_seconds',0)),604800))
        except Exception: disappear=0
        if theme not in {'default','dark','light','midnight','forest'}: theme='default'
        if wallpaper not in {'none','dots','gradient','paper','night'}: wallpaper='none'
        row=conn.execute('SELECT user_id FROM chat_preferences WHERE user_id=? AND peer_id=?',(session['user_id'],user_id)).fetchone()
        if row: conn.execute('UPDATE chat_preferences SET theme=?,wallpaper=?,disappearing_seconds=?,updated_at=? WHERE user_id=? AND peer_id=?',(theme,wallpaper,disappear,now,session['user_id'],user_id))
        else: conn.execute('INSERT INTO chat_preferences(user_id,peer_id,theme,wallpaper,disappearing_seconds,updated_at) VALUES(?,?,?,?,?,?)',(session['user_id'],user_id,theme,wallpaper,disappear,now))
        conn.commit(); conn.close(); return jsonify({'ok':True,'theme':theme,'wallpaper':wallpaper,'disappearing_seconds':disappear})
    row=conn.execute('SELECT theme,wallpaper,disappearing_seconds FROM chat_preferences WHERE user_id=? AND peer_id=?',(session['user_id'],user_id)).fetchone(); conn.close()
    return jsonify(dict(row) if row else {'theme':'default','wallpaper':'none','disappearing_seconds':0})

@app.route('/api/e2ee/safety-number/<int:user_id>')
def safety_number(user_id):
    if not user_required(): return jsonify({'error':'login_required'}),401
    import hashlib
    conn=get_db_connection(); a=get_public_key(conn,session['user_id']); b=get_public_key(conn,user_id); conn.close()
    if not a or not b: return jsonify({'error':'keys_not_ready'}),404
    material='|'.join(sorted([a,b])).encode(); digest=hashlib.sha256(material).hexdigest()
    grouped=' '.join(digest[i:i+5] for i in range(0,40,5))
    return jsonify({'safety_number':grouped,'fingerprint':digest})

@app.route('/api/e2ee/device', methods=['POST'])
def register_device():
    if not user_required(): return jsonify({'error':'login_required'}),401
    require_csrf(); data=request.get_json(silent=True) or {}; uid=session['user_id']
    device_id=str(data.get('device_id','')).strip(); identity=str(data.get('identity_public_key','')).strip()
    if not device_id or len(device_id)>128 or len(identity)<40: return jsonify({'error':'invalid_device'}),400
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); conn=get_db_connection()
    row=conn.execute('SELECT id FROM user_devices WHERE device_id=?',(device_id,)).fetchone()
    if row: conn.execute('UPDATE user_devices SET identity_public_key=?,device_name=?,signed_prekey=?,one_time_prekey=?,last_seen_at=?,revoked=0 WHERE device_id=?',(identity,str(data.get('device_name','Browser'))[:80],data.get('signed_prekey'),data.get('one_time_prekey'),now,device_id))
    else: conn.execute('INSERT INTO user_devices(user_id,device_id,device_name,identity_public_key,signed_prekey,one_time_prekey,created_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)',(uid,device_id,str(data.get('device_name','Browser'))[:80],identity,data.get('signed_prekey'),data.get('one_time_prekey'),now,now))
    conn.commit(); conn.close(); return jsonify({'ok':True,'device_id':device_id})

@app.route('/api/e2ee/devices')
def devices():
    if not user_required(): return jsonify({'error':'login_required'}),401
    conn=get_db_connection(); rows=conn.execute('SELECT device_id,device_name,created_at,last_seen_at,revoked FROM user_devices WHERE user_id=? ORDER BY last_seen_at DESC',(session['user_id'],)).fetchall(); conn.close(); return jsonify({'devices':[dict(x) for x in rows]})

@app.route('/api/e2ee/backup', methods=['GET','POST','DELETE'])
def encrypted_backup():
    if not user_required(): return jsonify({'error':'login_required'}),401
    uid=session['user_id']; conn=get_db_connection()
    if request.method=='GET':
        row=conn.execute('SELECT version,salt,iv,ciphertext,updated_at FROM encrypted_key_backups WHERE user_id=?',(uid,)).fetchone(); conn.close()
        return jsonify(dict(row) if row else {'backup':None})
    require_csrf()
    if request.method=='DELETE':
        conn.execute('DELETE FROM encrypted_key_backups WHERE user_id=?',(uid,)); conn.commit(); conn.close(); return jsonify({'ok':True})
    data=request.get_json(silent=True) or {}; salt=str(data.get('salt','')); iv=str(data.get('iv','')); ciphertext=str(data.get('ciphertext',''))
    if not salt or not iv or not ciphertext or len(ciphertext)>5000000: conn.close(); return jsonify({'error':'invalid_backup'}),400
    now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'); row=conn.execute('SELECT user_id FROM encrypted_key_backups WHERE user_id=?',(uid,)).fetchone()
    if row: conn.execute('UPDATE encrypted_key_backups SET salt=?,iv=?,ciphertext=?,updated_at=? WHERE user_id=?',(salt,iv,ciphertext,now,uid))
    else: conn.execute('INSERT INTO encrypted_key_backups(user_id,salt,iv,ciphertext,created_at,updated_at) VALUES(?,?,?,?,?,?)',(uid,salt,iv,ciphertext,now,now))
    conn.commit(); conn.close(); return jsonify({'ok':True})

# pass preferences to chat
old="    my_public_key = get_public_key(conn, current_user_id)\n    conn.commit(); conn.close()"
new="    my_public_key = get_public_key(conn, current_user_id)\n    pref = conn.execute('SELECT theme,wallpaper,disappearing_seconds FROM chat_preferences WHERE user_id=? AND peer_id=?',(current_user_id,user_id)).fetchone()\n    conn.commit(); conn.close()"
s=s.replace(old,new)
s=s.replace("my_public_key=my_public_key, other_public_key=other_public_key)","my_public_key=my_public_key, other_public_key=other_public_key, chat_pref=(dict(pref) if pref else {'theme':'default','wallpaper':'none','disappearing_seconds':0}))")
app.write_text(s)
