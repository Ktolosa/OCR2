import streamlit as st
import pandas as pd
import pdfplumber
from groq import Groq
import json
import re
from io import BytesIO

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Extractor Multi-Marca IA", layout="wide")

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

def consultar_groq(texto_pdf, api_key):
    client = Groq(api_key=api_key)
    
    # ---------------------------------------------------------
    # PROMPT ACTUALIZADO: INCLUYE DETECCIÓN DE MARCA (BRAND)
    # ---------------------------------------------------------
    prompt = f"""
    Actúa como un experto analista de facturas e inventarios.
    Tu objetivo es extraer una tabla estructurada de productos del siguiente texto de factura.

    TEXTO DEL DOCUMENTO:
    {texto_pdf}

    INSTRUCCIONES CRÍTICAS PARA LA EXTRACCIÓN:
    1. Debes generar un JSON con la lista de productos.
    2. DETECCIÓN DE MARCA (NUEVO): Busca la marca del producto (ej: Goodyear, Regal, MABE, GE, Samsung).
       - Si la marca aparece en el encabezado del documento (Logo o remitente) y no en cada línea, ASUME QUE APLICA PARA TODOS LOS ÍTEMS.
       - Si la marca está en la descripción del ítem, extráela.
    3. RELLENO DE ORIGEN: Si el país de origen (ej: Brazil, China, USA) aparece al inicio pero falta en las filas siguientes, rellénalo (Forward Fill).
    4. FILAS ROTAS: Une descripciones cortadas por saltos de línea.

    CAMPOS REQUERIDOS EN EL JSON:
    - "marca": (Ej: Goodyear, Regal, Mabe. NO lo dejes vacío si el documento tiene logo).
    - "cantidad": (Número).
    - "modelo": (Código, SKU, Part Number).
    - "descripcion": (Texto completo del producto).
    - "precio_unitario": (Número).
    - "origen": (Ej: Brazil, Mexico, USA).
    - "factura_origen": (Número de factura si está disponible).

    FORMATO DE SALIDA:
    Devuelve SOLO una lista de objetos JSON. Ejemplo:
    [
      {{
        "marca": "Goodyear",
        "cantidad": 40,
        "modelo": "111530",
        "descripcion": "215/60R17 EFFIGRIP SUV",
        "precio_unitario": 68.93,
        "origen": "Brazil",
        "factura_origen": "300098911"
      }}
    ]
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama3-70b-8192", 
            temperature=0.1, 
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        return f"Error Groq: {str(e)}"

def generar_excel(df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventario')
        
        workbook = writer.book
        worksheet = writer.sheets['Inventario']
        format_wrap = workbook.add_format({'text_wrap': True})
        
        # Ajustar anchos de columna
        worksheet.set_column('A:A', 15) # Marca
        worksheet.set_column('B:B', 10) # Cantidad
        worksheet.set_column('C:C', 15) # Modelo
        worksheet.set_column('D:D', 45, format_wrap) # Descripción
        worksheet.set_column('E:G', 15) # Precio, Origen
        
    return output.getvalue()

# --- INTERFAZ PRINCIPAL ---

st.title("🏭 Extractor Multi-Marca (Goodyear, Regal, MABE)")
st.markdown("Extrae inventarios detectando automáticamente la Marca y el Origen, incluso si están solo en el encabezado.")

# Sidebar
with st.sidebar:
    st.header("Configuración")
    api_key = st.text_input("Groq API Key", type="password")

archivo_subido = st.file_uploader("Cargar Factura PDF", type=["pdf"])

if archivo_subido and api_key:
    if st.button("Procesar Factura"):
        with st.spinner('Detectando Marcas, Orígenes y Productos...'):
            
            texto_crudo = extraer_texto_pdf(archivo_subido)
            respuesta_ia = consultar_groq(texto_crudo, api_key)
            json_str = limpiar_json_response(respuesta_ia)
            
            try:
                data = json.loads(json_str)
                
                if isinstance(data, list) and len(data) > 0:
                    df = pd.DataFrame(data)
                    
                    # Reordenar columnas para que Marca salga primero
                    cols_order = ['marca', 'modelo', 'descripcion', 'cantidad', 'precio_unitario', 'origen', 'factura_origen']
                    # Aseguramos que existan las columnas antes de reordenar
                    cols_existentes = [c for c in cols_order if c in df.columns]
                    df = df[cols_existentes]
                    
                    st.success(f"✅ Procesado. Se detectaron productos de: {df['marca'].unique()}")
                    
                    st.dataframe(df)
                    
                    excel_bytes = generar_excel(df)
                    st.download_button(
                        label="📥 Descargar Excel Completo",
                        data=excel_bytes,
                        file_name="Inventario_Procesado.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.warning("No se encontraron datos tablares.")
                    st.text(respuesta_ia)
                        
            except Exception as e:
                st.error("Error en el formato de respuesta de la IA.")
                st.text(respuesta_ia)

elif archivo_subido and not api_key:
    st.info("Por favor ingresa tu API Key.")
