import streamlit as st
import pandas as pd
from groq import Groq
from pdf2image import convert_from_path
import tempfile
import os
import json
import time
import base64
import io
from datetime import date # Para la fecha en el nombre del archivo

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Nexus Extractor (Motor Groq)", layout="wide")
st.title("⚡ Nexus Extractor: Motor Llama 4 Vision (Groq)")

# 1. Configurar Cliente Groq
if "GROQ_API_KEY" in st.secrets:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("❌ Falta la API KEY de Groq. Configura 'GROQ_API_KEY' en secrets.")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Eres un experto en extracción de datos. Analiza la imagen de la factura.
        REGLA DE FILTRADO:
        1. Si el documento dice explícitamente "Duplicado" o "Copia", marca "tipo_documento" como "Copia" y deja "items" vacío.
        2. Si dice "Original" o no especifica, extrae todo.
        Responde SOLAMENTE con un JSON válido:
        {"tipo_documento": "Original/Copia", "numero_factura": "Invoice #", "fecha": "YYYY-MM-DD", "orden_compra": "PO #", "proveedor": "Vendor Name", "cliente": "Sold To", "items": [{"modelo": "Model No", "descripcion": "Description", "cantidad": 0, "precio_unitario": 0.00, "total_linea": 0.00}], "total_factura": 0.00}
    """,
    "Factura RadioShack": """
        Analiza esta factura de RadioShack. Extrae datos en JSON. Usa SKU como modelo.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
    """,
    "Factura Mabe": """
        Analiza esta factura de Mabe. Extrae datos en JSON. Usa CODIGO MABE como modelo. Ignora impuestos.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
    """,
    "Factura Goodyear": """
        Analiza esta factura de Goodyear.
        
        INSTRUCCIONES CRÍTICAS DE LECTURA:
        1. NÚMERO DE FACTURA:
           - Busca "INVOICE NUMBER". Si NO aparece en esta página, devuelve null o "CONTINUACION".

        2. TABLA DE ITEMS:
           - Mapeo: 'Code' -> modelo, 'Origin' -> origen (PAÍS COMPLETO), 'Description' -> descripcion, 'Qty' -> cantidad, 'Unit Value' -> precio_unitario.
           - Si la info está rota en dos líneas, únelas lógicamente.

        Responde SOLAMENTE con este JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "...",
            "fecha": "...",
            "orden_compra": "...",
            "proveedor": "Goodyear International Corporation",
            "cliente": "...",
            "items": [
                {
                    "modelo": "...",
                    "origen": "País (ej: Brazil)", 
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """
}

# ==========================================
# 🛠️ FUNCIONES AUXILIARES
# ==========================================
def codificar_imagen(image):
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def generar_excel_dpr(df_items, nombre_proveedor):
    """
    Genera un archivo Excel con formato DPR específico.
    """
    output = io.BytesIO()
    
    # --- CONFIGURACIÓN DE COLUMNAS DEL DPR ---
    # AJUSTA ESTA LISTA CON LOS ENCABEZADOS EXACTOS DE TU CSV/DPR
    # Mantén el orden exacto de tu archivo ejemplo.
    columnas_dpr = [
        "ITEM",           # A
        "CODIGO",         # B (modelo)
        "DESCRIPCION",    # C (descripcion)
        "CANTIDAD",       # D (cantidad)
        "PRECIO UNIT",    # E (precio_unitario)
        "TOTAL",          # F (total_linea)
        "ORIGEN",         # G (origen)
        "TLC",            # H (Vacio)
        "PESO NETO",      # I (Vacio)
        "PESO BRUTO",     # J (Vacio)
        "FACTURA",        # K
        "OBSERVACIONES"   # L (Vacio)
    ]
    
    # Creamos el escritor de Excel usando XlsxWriter
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("DPR_DATA")
        
        # Formatos
        header_format = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
        
        # 1. Escribir Encabezados
        for col_num, value in enumerate(columnas_dpr):
            worksheet.write(0, col_num, value, header_format)
            
        # 2. Escribir Datos
        for row_num, item in enumerate(df_items.to_dict('records'), 1):
            # Mapeo de datos extraídos a las columnas del DPR
            # (Ajusta los índices si cambias el orden de columnas_dpr)
            
            worksheet.write(row_num, 0, row_num)                  # A: ITEM (Consecutivo)
            worksheet.write(row_num, 1, item.get('modelo', ''))   # B: CODIGO
            worksheet.write(row_num, 2, item.get('descripcion', '')) # C: DESCRIPCION
            worksheet.write(row_num, 3, item.get('cantidad', 0))  # D: CANTIDAD
            worksheet.write(row_num, 4, item.get('precio_unitario', 0)) # E: PRECIO
            worksheet.write(row_num, 5, item.get('total_linea', 0)) # F: TOTAL
            worksheet.write(row_num, 6, item.get('origen', ''))   # G: ORIGEN (Nuevo!)
            worksheet.write(row_num, 7, "")                       # H: TLC (Vacío)
            worksheet.write(row_num, 8, "")                       # I: PESO NETO (Vacío)
            worksheet.write(row_num, 9, "")                       # J: PESO BRUTO (Vacío)
            worksheet.write(row_num, 10, item.get('Factura_Origen', '')) # K: FACTURA
            worksheet.write(row_num, 11, "")                      # L: OBS
        
        # 3. Ocultar Columnas (Si tu DPR tiene columnas ocultas, hazlo aquí)
        # Ejemplo: Si la columna "TLC" (H -> indice 7) debe estar oculta:
        # worksheet.set_column(7, 7, None, {'hidden': True}) 
        
        # Ajustar ancho de columnas básico
        worksheet.set_column(0, 0, 5)  # Item
        worksheet.set_column(1, 1, 15) # Codigo
        worksheet.set_column(2, 2, 40) # Descripcion
        
    return output.getvalue()

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS
# ==========================================
def analizar_pagina(image, prompt_sistema):
    try:
        base64_image = codificar_imagen(image)
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_sistema},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ],
            model="meta-llama/llama-4-scout-17b-16e-instruct", 
            temperature=0.1,
            max_tokens=4096,
            stream=False,
            response_format={"type": "json_object"}, 
        )
        texto_respuesta = chat_completion.choices[0].message.content
        return json.loads(texto_respuesta), None
    except Exception as e:
        if "model_decommissioned" in str(e):
             return {}, "⚠️ Modelo antiguo. Contacta soporte."
        return {}, f"Error Groq: {str(e)}"

# ==========================================
# ⚙️ PROCESAMIENTO
# ==========================================
def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF: {e}"

    items_locales = []
    resumen_local = []
    
    ultimo_numero_factura = "S/N"
    
    my_bar = st.progress(0, text=f"Analizando {filename}...")

    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
        
        elif not data or "copia" in str(data.get("tipo_documento", "")).lower():
            pass 
        else:
            factura_actual = str(data.get("numero_factura", "")).strip()
            
            if not factura_actual or factura_actual.lower() in ["none", "null", "continuacion", "pendiente"] or len(factura_actual) < 3:
                factura_id = ultimo_numero_factura
            else:
                factura_id = factura_actual
                ultimo_numero_factura = factura_actual

            # Guardamos Items
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Archivo_Origen"] = filename
                    item["Factura_Origen"] = factura_id
                    if "origen" not in item: item["origen"] = "" 
                    items_locales.append(item)
            
            # Guardamos Resumen
            ya_existe = any(d['Factura'] == factura_id and d['Archivo'] == filename for d in resumen_local)
            if not ya_existe and factura_id != "S/N":
                resumen_local.append({
                    "Archivo": filename,
                    "Factura": factura_id,
                    "Total": data.get("total_factura"),
                    "Cliente": data.get("cliente")
                })
        
        my_bar.progress((i + 1) / len(images))
        time.sleep(0.5) 

    my_bar.empty()
    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.success("⚡ Motor Groq (Llama 4 Vision)")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar con Groq"):
    gran_acumulado = []
    
    # Nombre del proveedor para el archivo (se basa en la selección)
    nombre_proveedor_archivo = "Goodyear" if "Goodyear" in tipo_pdf else "Proveedor"
    
    st.divider()
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Enviando a Groq LPU..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    df = pd.DataFrame(items)
                    st.success(f"✅ {len(items)} items extraídos.")
                    
                    # Mostrar tabla
                    cols_to_show = ["modelo", "descripcion", "cantidad", "precio_unitario", "origen", "Factura_Origen"]
                    # Filtramos solo columnas que existan para evitar errores si cambia el prompt
                    cols_finales = [c for c in cols_to_show if c in df.columns]
                    st.dataframe(df[cols_finales], use_container_width=True)
                    
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos.")

    if gran_acumulado:
        st.divider()
        st.subheader("📥 Zona de Descargas")
        
        # 1. Descarga CSV Estándar (Todo junto)
        df_master = pd.DataFrame(gran_acumulado)
        csv = df_master.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar CSV Bruto", csv, "extraccion_raw.csv", "text/csv")
        
        # 2. Descarga DPR ESPECIAL (Solo si es Goodyear o se solicita)
        if "Goodyear" in tipo_pdf:
            fecha_hoy = date.today().strftime("%Y-%m-%d")
            nombre_archivo_dpr = f"DPR_{nombre_proveedor_archivo}_{fecha_hoy}.xlsx"
            
            excel_data = generar_excel_dpr(df_master, nombre_proveedor_archivo)
            
            st.download_button(
                label=f"📄 Descargar Formato DPR ({nombre_archivo_dpr})",
                data=excel_data,
                file_name=nombre_archivo_dpr,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
