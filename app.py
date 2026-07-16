import os
import pandas as pd
import sqlite3
import unicodedata
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'clave_secreta_para_sesiones' 
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'xlsx', 'xls'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- CONFIGURACIÓN DE BASE DE DATOS ---
def init_db():
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    # Crea la tabla si no existe
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)''')
    
    # Crear el usuario admin por defecto si la base de datos está vacía
    c.execute("SELECT * FROM usuarios WHERE username='admin'")
    if not c.fetchone():
        hashed_pw = generate_password_hash('1234')
        c.execute("INSERT INTO usuarios (username, password) VALUES (?, ?)", ('admin', hashed_pw))
    
    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect('usuarios.db')
    conn.row_factory = sqlite3.Row
    return conn

# --- FUNCIONES DE ANÁLISIS ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def analizar_asistencias(filepath, filtros_pares=None):
    df = pd.read_excel(filepath)
    df.columns = df.columns.str.strip()
    df['Fecha'] = pd.to_datetime(df['Marca temporal'], dayfirst=True, errors='coerce')
    df = df[~df['Fecha'].dt.month.isin([1, 12])]
    
    if filtros_pares:
        mascara_global = pd.Series(False, index=df.index)
        tiene_al_menos_un_filtro = False

        for par in filtros_pares:
            f = par.get('fecha')
            c = par.get('curso')
            if f or c:
                tiene_al_menos_un_filtro = True
                mascara_actual = pd.Series(True, index=df.index)
                if f:
                    fecha_dt = pd.to_datetime(f).date()
                    mascara_actual = mascara_actual & (df['Fecha'].dt.date == fecha_dt)
                if c:
                    mascara_actual = mascara_actual & (df['Curso al que asiste'].str.contains(c, case=False, na=False))
                mascara_global = mascara_global | mascara_actual
        if tiene_al_menos_un_filtro:
            df = df[mascara_global]

    if df.empty:
        return []

    # --- FUNCIÓN AUXILIAR PARA NORMALIZAR TEXTO (ELIMINA ACENTOS Y DOBLES ESPACIOS) ---
    def limpiar_texto(val):
        if pd.isna(val):
            return ""
        # Limpia espacios dobles en medio y extremos del texto
        val = " ".join(str(val).strip().split())
        # Descompone los caracteres en sus elementos base para remover los acentos/diacríticos
        return "".join(c for c in unicodedata.normalize('NFD', val) if unicodedata.category(c) != 'Mn').upper()

    # Creamos una columna interna oculta para poder agrupar sin variaciones ortográficas
    df['Nombre_Normalizado'] = df['Nombre completo del asistente'].apply(limpiar_texto)
    # ---------------------------------------------------------------------------------

    df['Periodo'] = df['Fecha'].dt.to_period('M')
    resultados = []
    
    # Agrupamos utilizando la columna limpia para agrupar duplicados de nombres
    for nombre_limpio, grupo in df.groupby('Nombre_Normalizado'):
        grupo = grupo.sort_values('Fecha')
        
        # Obtenemos la primera ortografía del nombre original para pintarlo bonito en pantalla
        nombre_original = grupo['Nombre completo del asistente'].iloc[0]
        
        cursos_unicos = grupo['Curso al que asiste'].unique().tolist()
        amount_cursos_distintos = len(cursos_unicos)
        periodos = sorted(grupo['Periodo'].dropna().unique())
        meses_registrados = len(periodos)
        
        es_continuo = True
        if meses_registrados > 1:
            for i in range(1, meses_registrados):
                if (periodos[i] - periodos[i-1]).n != 1:
                    es_continuo = False
                    break
        elif meses_registrados == 0:
            es_continuo = False
            
        cumple_meta = False
        if es_continuo and meses_registrados >= 4 and amount_cursos_distintos >= 4:
            cumple_meta = True
        
        resultados.append({
            'nombre': nombre_original,
            'cantidad_asistencias': len(grupo), # Suma total de registros del asistente unificado
            'cursos': ", ".join(cursos_unicos),
            'cantidad_cursos_distintos': amount_cursos_distintos,
            'es_continuo': "Sí" if es_continuo else "No",
            'meses_registrados': meses_registrados,
            'cumple_meta': cumple_meta
        })
    
    return sorted(resultados, key=lambda x: x['cantidad_asistencias'], reverse=True)

# --- RUTAS DE LA PÁGINA ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        user = conn.execute("SELECT * VALUES FROM usuarios WHERE username = ?", (usuario,)).fetchone()
        conn.close()
        
        # Comparamos la contraseña usando el sistema de encriptación
        if user and check_password_hash(user['password'], password):
            session['logged_in'] = True
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos. Intenta de nuevo.')
    
    return render_template('login.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        usuario = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        user_exists = conn.execute("SELECT * FROM usuarios WHERE username = ?", (usuario,)).fetchone()
        
        if user_exists:
            flash('Ese nombre de usuario ya existe. Por favor elige otro.')
        else:
            hashed_pw = generate_password_hash(password)
            conn.execute("INSERT INTO usuarios (username, password) VALUES (?, ?)", (usuario, hashed_pw))
            conn.commit()
            flash('Usuario registrado exitosamente. Ahora puedes iniciar sesión.')
            conn.close()
            return redirect(url_for('login'))
        conn.close()
        
    return render_template('registro.html')

@app.route('/recuperar', methods=['GET', 'POST'])
def recuperar():
    if request.method == 'POST':
        usuario = request.form.get('username')
        nueva_password = request.form.get('new_password')
        
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM usuarios WHERE username = ?", (usuario,)).fetchone()
        
        if user:
            hashed_pw = generate_password_hash(nueva_password)
            conn.execute("UPDATE usuarios SET password = ? WHERE username = ?", (hashed_pw, usuario))
            conn.commit()
            flash('Contraseña actualizada con éxito. Inicia sesión con tu nueva contraseña.')
            conn.close()
            return redirect(url_for('login'))
        else:
            flash('No se encontró ningún usuario con ese nombre.')
        conn.close()
        
    return render_template('recuperar.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    resultados = None
    busqueda_realizada = False
    valores_formulario = {}
    
    if request.method == 'POST':
        filtros_pares = []
        for i in range(1, 5):
            fecha = request.form.get(f'fecha_{i}', '')
            curso = request.form.get(f'curso_{i}', '')
            valores_formulario[f'fecha_{i}'] = fecha
            valores_formulario[f'curso_{i}'] = curso
            filtros_pares.append({'fecha': fecha, 'curso': curso})

        if 'file' not in request.files:
            flash('No se seleccionó ningún archivo')
            return render_template('dashboard.html', resultados=None, busqueda_realizada=False, valores_formulario=valores_formulario)
            
        file = request.files['file']
        if file.filename == '':
            flash('No se seleccionó ningún archivo')
            return render_template('dashboard.html', resultados=None, busqueda_realizada=False, valores_formulario=valores_formulario)
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            try:
                resultados = analizar_asistencias(filepath, filtros_pares)
                busqueda_realizada = True
            except Exception as e:
                flash(f'Error al procesar el Excel. Detalle: {e}')
            
            if os.path.exists(filepath):
                os.remove(filepath)

    return render_template('dashboard.html', resultados=resultados, busqueda_realizada=busqueda_realizada, valores_formulario=valores_formulario)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
