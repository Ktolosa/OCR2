import streamlit as st
import pandas as pd
import pdfplumber
from groq import Groq
import json
import re
from io import BytesIO

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Extractor IA Multi-Marca", layout="wide")

# --- GESTIÓN DE API KEY (SECRETS) ---
try:
    # Intenta leer la clave de los secretos de Streamlit
    api_key = st.secrets["GROQ_API_KEY"]
    usando_secrets = True
except:
    # Si falla, dejaremos que el usuario la ingrese manualmente
    api_key = None
    usando_secrets = False

# --- FUNCIONES AUXILIARES ---

def extraer_texto_pdf(pdf_file):
    """Convierte el PDF a texto plano respetando el layout visual"""
    texto_completo = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            texto_extraido = page.extract_text()
            if texto_extraido:
                texto_completo += f"--- PÁGINA {page.page_number} ---\n"
                texto_completo += texto_extraido + "\n"
    return texto_completo

def limpiar_json_response(response_text):
    """Limpia la respuesta de Groq para obtener solo el JSON válido"""
    match = re.search(r'\[.*\]', response_text, re.DOTALL)
    if match:
        return match.group(0)
    return response_text

def consultar_groq(texto_pdf, api_key_valida):
    client = Groq(api_key=api_key_valida)
    
    # ---------------------------------------------------------
    # PROMPT MAESTRO: MARCAS + CORRECCIONES
    # ---------------------------------------------------------
    prompt = f"""
    Eres un experto en extracción de datos de facturas (Invoice OCR Expert).
    Tu tarea es leer el texto de esta factura y generar una lista estructurada de productos.

    TEXTO DE LA FACTURA:
    {texto_pdf}

    INSTRUCCIONES CLAVE:
    1. DETECCIÓN DE MARCA: Identifica la marca (Brand) de los productos (Ej: Goodyear, Regal, MABE, Samsung, etc.).
       - Si la marca aparece en el logo/encabezado y aplica para todo, úsala en todos los ítems.
       - Si no encuentras marca explícita, usa "Genérico" o el nombre del proveedor.
    2. RELLENO DE ORIGEN (CRÍTICO): Si el campo "Origen" (Origin/Country) está presente en el encabezado o primera fila (ej: "Brazil") pero vacío en las siguientes, RELLÉNALO con el valor anterior.
    3. FILAS ROTAS: Si la descripción o modelo se corta en dos líneas, únelas.

    CAMPOS JSON REQUERIDOS:
    - "marca": (Ej: Goodyear).
    - "cantidad": (Número entero).
    - "modelo": (Código/SKU).
    - "descripcion": (Nombre del producto completo).
    - "precio_unitario": (Número decimal).
    - "origen": (País).
    - "factura_origen": (Número de factura/referencia si existe).

    SALIDA:
    ÚNICAMENTE una lista de objetos JSON válida. Sin texto extra.
    Ejemplo:
    [
      {{ "marca": "Goodyear", "cantidad": 10, "modelo": "123", "descripcion": "Llantas...", "precio_unitario": 50.5, "origen": "Brazil", "factura_origen": "F-001" }}
    ]
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-70b-8192", 
            temperature=0.1, 
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        return f"Error API: {str(e)}"

def generar_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario')
        workbook = writer.book
        worksheet = writer.sheets['Inventario']
        format_wrap = workbook.add_format({'text_wrap': True})
        
        # Formato visual
        worksheet.set_column('A:A', 15) # Marca
        worksheet.set_column('B:C', 12) # Cantidad/Modelo
        worksheet.set_column('D:D', 50, format_wrap) # Descripción Larga
        worksheet.set_column('E:G', 15) 
        
    return output.getvalue()

# --- INTERFAZ GRAFICA ---

st.title("🏭 Extractor Inteligente (Goodyear/Regal/MABE)")

# Sidebar Condicional
with st.sidebar:
    st.header("Estado del Sistema")
    if usando_secrets:
        st.success("🔑 API Key cargada desde el sistema.")
    else:
        st.warning("⚠️ No se detectó API Key en secrets.")
        api_key = st.text_input("Ingresa tu Groq API Key", type="password")

st.markdown("---")

archivo_subido = st.file_uploader("Cargar PDF", type=["pdf"])

if archivo_subido:
    if not api_key:
        st.error("❌ Falta la API Key para continuar.")
    else:
        if st.button("Procesar Documento"):
            with st.spinner('Analizando documento con IA (Llama 3)...'):
                
                # 1. Leer PDF
                texto = extraer_texto_pdf(archivo_subido)
                
                # 2. Consultar IA
                respuesta = consultar_groq(texto, api_key)
                json_str = limpiar_json_response(respuesta)
                
                # 3. Procesar Datos
                try:
                    data = json.loads(json_str)
                    if isinstance(data, list) and len(data) > 0:
                        df = pd.DataFrame(data)
                        
                        # Reordenar columnas para prioridad visual
                        cols_deseadas = ['marca', 'modelo', 'descripcion', 'cantidad', 'precio_unitario', 'origen', 'factura_origen']
                        cols_finales = [c for c in cols_deseadas if c in df.columns]
                        df = df[cols_finales]

                        st.success(f"✅ Extracción exitosa. Marcas detectadas: {', '.join(df['marca'].astype(str).unique())}")
                        
                        st.dataframe(df)
                        
                        # 4. Excel
                        excel_bytes = generar_excel(df)
                        st.download_button(
                            label="📥 Descargar Excel (.xlsx)",
                            data=excel_bytes,
                            file_name="Inventario_Procesado.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                    else:
                        st.warning("La IA no encontró productos en el documento.")
                        st.text(respuesta) # Debug
                except Exception as e:
                    st.error("Error al interpretar la respuesta de la IA.")
                    st.text(respuesta) # Debug
