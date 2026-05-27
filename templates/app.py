import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'clave_secreta_para_sesiones' 
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'xlsx', 'xls'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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

    df['Periodo'] = df['Fecha'].dt.to_period('M')
    resultados = []
    
    for nombre, grupo in df.groupby('Nombre completo del asistente'):
        grupo = grupo.sort_values('Fecha')
        
        cursos_unicos = grupo['Curso al que asiste'].unique().tolist()
        cantidad_cursos_distintos = len(cursos_unicos)
        
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
        if es_continuo and meses_registrados >= 4 and cantidad_cursos_distintos >= 4:
            cumple_meta = True
        
        resultados.append({
            'nombre': nombre,
            'cantidad_asistencias': len(grupo),
            'cursos': ", ".join(cursos_unicos),
            'cantidad_cursos_distintos': cantidad_cursos_distintos,
            'es_continuo': "Sí" if es_continuo else "No",
            'meses_registrados': meses_registrados,
            'cumple_meta': cumple_meta
        })
    
    resultados = sorted(resultados, key=lambda x: x['cantidad_asistencias'], reverse=True)
        
    return resultados

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('username')
        password = request.form.get('password')
        
        if usuario == 'admin' and password == '1234':
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            flash('Credenciales incorrectas')
    
    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    resultados = None
    busqueda_realizada = False
    
    # NUEVO: Diccionario para guardar lo que el usuario escribió
    valores_formulario = {}
    
    if request.method == 'POST':
        filtros_pares = []
        for i in range(1, 5):
            fecha = request.form.get(f'fecha_{i}', '')
            curso = request.form.get(f'curso_{i}', '')
            
            # Guardamos los valores en el diccionario
            valores_formulario[f'fecha_{i}'] = fecha
            valores_formulario[f'curso_{i}'] = curso
            
            filtros_pares.append({'fecha': fecha, 'curso': curso})

        if 'file' not in request.files:
            flash('No se seleccionó ningún archivo')
            # Enviamos los valores de regreso aunque falle el archivo
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

    # NUEVO: Le pasamos 'valores_formulario' a la plantilla HTML
    return render_template('dashboard.html', resultados=resultados, busqueda_realizada=busqueda_realizada, valores_formulario=valores_formulario)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)