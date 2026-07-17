import os
import pandas as pd
import sqlite3
import unicodedata
from datetime import datetime
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
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)''')
    
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

    def limpiar_texto(val):
        if pd.isna(val):
            return ""
        val = " ".join(str(val).strip().split())
        return "".join(c for c in unicodedata.normalize('NFD', val) if unicodedata.category(c) != 'Mn').upper()

    df['Nombre_Normalizado'] = df['Nombre completo del asistente'].apply(limpiar_texto)
    df['Periodo'] = df['Fecha'].dt.to_period('M')
    
    resultados = []
    for nombre_limpio, grupo in df.groupby('Nombre_Normalizado'):
        grupo = grupo.sort_values('Fecha')
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
            'cantidad_asistencias': len(grupo),
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
        user = conn.execute("SELECT * FROM usuarios WHERE username = ?", (usuario,)).fetchone()
        conn.close()
        
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
        if conn.execute("SELECT * FROM usuarios WHERE username = ?", (usuario,)).fetchone():
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

    if request.method == 'GET':
        session.pop('candidatos_aptos', None)
        return render_template('dashboard.html', resultados=None, busqueda_realizada=False, valores_formulario={})

    resultados = None
    busqueda_realizada = False
    valores_formulario = {}
    
    if request.method == 'POST':
        filtros_pares = []
        cursos_guardados = []
        fechas_guardadas = []
        fecha_4 = ""

        for i in range(1, 5):
            fecha = request.form.get(f'fecha_{i}', '')
            curso = request.form.get(f'curso_{i}', '')
            valores_formulario[f'fecha_{i}'] = fecha
            valores_formulario[f'curso_{i}'] = curso
            filtros_pares.append({'fecha': fecha, 'curso': curso})
            
            if curso:
                cursos_guardados.append(curso)
            if fecha:
                fechas_guardadas.append(fecha)
            if i == 4:
                fecha_4 = fecha

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
                
                # GUARDAR VARIABLES PARA EL CERTIFICADO
                session['candidatos_aptos'] = [res['nombre'] for res in resultados if res['cumple_meta']]
                session['cert_cursos'] = cursos_guardados
                session['cert_fechas'] = fechas_guardadas
                session['cert_fecha_final'] = fecha_4
                
            except Exception as e:
                flash(f'Error al procesar el Excel. Detalle: {e}')
            
            if os.path.exists(filepath):
                os.remove(filepath)

    return render_template('dashboard.html', resultados=resultados, busqueda_realizada=busqueda_realizada, valores_formulario=valores_formulario)

@app.route('/calificar_examenes', methods=['GET', 'POST'])
def calificar_examenes():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    candidatos = session.get('candidatos_aptos', [])
    if not candidatos:
        flash('⚠️ No hay candidatos aptos en memoria. Por favor, sube y analiza las asistencias primero.')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        archivos = request.files.getlist('file_examenes')
        if not archivos or archivos[0].filename == '':
            flash('No se seleccionó ningún archivo de exámenes.')
            return redirect(request.url)

        def limpiar_texto(val):
            if pd.isna(val): return ""
            val = " ".join(str(val).strip().split())
            return "".join(c for c in unicodedata.normalize('NFD', val) if unicodedata.category(c) != 'Mn').upper()

        def extraer_calificacion(val):
            if pd.isna(val): return 0.0
            try:
                numero = str(val).split('/')[0].strip()
                return float(numero)
            except:
                return 0.0

        notas_por_candidato = {limpiar_texto(nombre): [] for nombre in candidatos}
        nombres_examenes = []

        try:
            for file in archivos:
                if file and allowed_file(file.filename):
                    nombre_materia = file.filename.rsplit('.', 1)[0]
                    nombres_examenes.append(nombre_materia)

                    df_examenes = pd.read_excel(file)
                    df_examenes.columns = df_examenes.columns.str.strip()

                    col_nombre = next((c for c in df_examenes.columns if 'nombre' in c.lower()), None)
                    col_puntuacion = next((c for c in df_examenes.columns if 'puntuaci' in c.lower()), None)

                    if col_nombre and col_puntuacion:
                        df_examenes['Nombre_Limpio'] = df_examenes[col_nombre].apply(limpiar_texto)
                        for nombre_limpio in notas_por_candidato.keys():
                            fila = df_examenes[df_examenes['Nombre_Limpio'] == nombre_limpio]
                            if not fila.empty:
                                val = fila[col_puntuacion].iloc[0]
                                notas_por_candidato[nombre_limpio].append(extraer_calificacion(val))
                            else:
                                notas_por_candidato[nombre_limpio].append(0.0)
                    else:
                        for nombre_limpio in notas_por_candidato.keys():
                            notas_por_candidato[nombre_limpio].append(0.0)

            while len(nombres_examenes) < 4:
                nombres_examenes.append(f"Examen {len(nombres_examenes) + 1}")

            evaluaciones = []
            for nombre in candidatos:
                nombre_limpio = limpiar_texto(nombre)
                notas = notas_por_candidato[nombre_limpio]
                while len(notas) < 4:
                    notas.append(0.0)

                if min(notas[:4]) >= 8:
                    estado = "Certificado"
                else:
                    estado = "No Certificado (Requiere mínimo 8 en todos)"

                evaluaciones.append({'nombre': nombre, 'estado': estado, 'notas': notas[:4]})

            flash('¡Exámenes cruzados y evaluados con éxito!')
            return render_template('calificar_examenes.html', candidatos=candidatos, evaluaciones=evaluaciones, nombres_examenes=nombres_examenes[:4])

        except Exception as e:
            flash(f'Error al procesar los Excels. Detalle: {e}')
            return redirect(request.url)

    return render_template('calificar_examenes.html', candidatos=candidatos, evaluaciones=None, nombres_examenes=None)

@app.route('/certificado/<nombre>')
def certificado(nombre):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    meses_dict = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    
    # 1. TEXTO DE LOS CURSOS
    cursos_lista = session.get('cert_cursos', [])
    if len(cursos_lista) > 1:
        cursos_str = ", ".join(cursos_lista[:-1]) + " y " + cursos_lista[-1]
    elif len(cursos_lista) == 1:
        cursos_str = cursos_lista[0]
    else:
        cursos_str = "los cursos correspondientes"

    # 2. TEXTO DE LOS MESES
    fechas_lista = session.get('cert_fechas', [])
    meses_unicos = []
    for f in fechas_lista:
        try:
            m = datetime.strptime(f, '%Y-%m-%d').month
            nombre_mes = meses_dict[m - 1]
            if nombre_mes not in meses_unicos:
                meses_unicos.append(nombre_mes)
        except:
            pass
            
    if len(meses_unicos) > 1:
        meses_str = ", ".join(meses_unicos[:-1]) + " y " + meses_unicos[-1]
    elif len(meses_unicos) == 1:
        meses_str = meses_unicos[0]
    else:
        meses_str = ""

    # 3. FECHA FINAL DEL 4TO CURSO
    fecha_4 = session.get('cert_fecha_final', '')
    if fecha_4:
        try:
            dt = datetime.strptime(fecha_4, '%Y-%m-%d')
            fecha_formateada = f"{dt.day} de {meses_dict[dt.month - 1]} del {dt.year}"
        except:
            fecha_formateada = ""
    else:
        hoy = datetime.now()
        fecha_formateada = f"{hoy.day} de {meses_dict[hoy.month - 1]} del {hoy.year}"
    
    return render_template('certificado.html', nombre=nombre, cursos=cursos_str, meses=meses_str, fecha_final=fecha_formateada)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
