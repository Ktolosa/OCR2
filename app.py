import streamlit as st
import pandas as pd
from groq import Groqimport streamlit as st
import pandas as pd
from groq import Groq
from pdf2image import convert_from_path
import tempfile
import os
import json
import time
import base64
import io

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
# 🧠 DEFINICIÓN DE PROMPTS (MEJORADO GOODYEAR)
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
           - Busca "INVOICE NUMBER" (ej: 300098911).
           - IMPORTANTE: Si en esta página NO aparece el texto "INVOICE NUMBER" (como en páginas de continuación), devuelve null o "CONTINUACION". ¡NO uses códigos de producto como número de factura!

        2. TABLA DE ITEMS (LAYOUT COMPLEJO):
           - Caso Normal (Pág 1): Todo en una línea.
           - Caso Roto (Pág 2+): La información del item se divide en dos líneas.
             Linea A: "215/60R17 EFFIGRIP SUV..." (Esto es la DESCRIPCIÓN)
             Linea B: "40.000   111530   68.93..." (Esto es: Cantidad, CÓDIGO, Precio)
           
           - TU TAREA: Si ves este formato roto, reconstruye el item:
             - 'modelo': Extrae el número de 6 dígitos de la segunda línea (ej: 111530).
             - 'descripcion': Extrae el texto de la primera línea.
             - 'cantidad': El primer número de la segunda línea.

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
    
    # VARIABLE PARA ARRASTRAR EL NÚMERO DE FACTURA ENTRE PÁGINAS
    ultimo_numero_factura = "S/N"
    
    my_bar = st.progress(0, text=f"Analizando {filename}...")

    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
        
        # Filtro de Copias
        elif not data or "copia" in str(data.get("tipo_documento", "")).lower():
            pass 
        else:
            # LÓGICA INTELIGENTE DE FACTURA
            factura_actual = str(data.get("numero_factura", "")).strip()
            
            # Si la IA no encontró factura o dice "CONTINUACION", usamos la de la página anterior
            if not factura_actual or factura_actual.lower() in ["none", "null", "continuacion", "pendiente"] or len(factura_actual) < 3:
                factura_id = ultimo_numero_factura
            else:
                factura_id = factura_actual
                ultimo_numero_factura = factura_actual # Actualizamos para las siguientes páginas

            # Guardamos Items
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Archivo_Origen"] = filename
                    item["Factura_Origen"] = factura_id
                    items_locales.append(item)
            
            # Guardamos Resumen (Solo si encontramos una factura nueva o es la pág 1)
            # Evitamos duplicados en la tabla resumen
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
                    st.dataframe(df, use_container_width=True)
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos (Copia o vacío).")

    if gran_acumulado:
        st.divider()
        csv = pd.DataFrame(gran_acumulado).to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Todo (CSV)", csv, "extraccion_groq.csv", "text/csv")
from pdf2image import convert_from_path
import tempfile
import os
import json
import time
import base64
import io

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Nexus Extractor (Excel)", layout="wide")
st.title("⚡ Nexus Extractor: Motor Llama 3.2 Vision (Excel)")

# 1. Configurar Cliente Groq
# Intenta obtener la key de secrets, si no, busca una variable de entorno o input manual
api_key = st.secrets.get("GROQ_API_KEY")
if not api_key:
    st.error("❌ Falta la API KEY de Groq. Configura 'GROQ_API_KEY' en secrets.")
    st.stop()

client = Groq(api_key=api_key)

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
        {"tipo_documento": "Original/Copia", "numero_factura": "Invoice #", "fecha": "YYYY-MM-DD", "orden_compra": "PO #", "proveedor": "Vendor Name", "cliente": "Sold To", "items": [{"modelo": "Model No", "descripcion": "Description", "cantidad": 0, "precio_unitario": 0.00, "origen": ""}], "total_factura": 0.00}
    """,
    "Factura RadioShack": """
        Analiza esta factura de RadioShack. Extrae datos en JSON. Usa SKU como modelo.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Mabe": """
        Analiza esta factura de Mabe. Extrae datos en JSON. Usa CODIGO MABE como modelo. Ignora impuestos.
        JSON: {"tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "origen": ""}], "total_factura": 0.0}
    """,
    "Factura Goodyear": """
        Analiza esta factura de Goodyear.
        
        INSTRUCCIONES CRÍTICAS DE LECTURA:
        1. NÚMERO DE FACTURA:
           - Busca "INVOICE NUMBER" (ej: 300098911).
           - IMPORTANTE: Si en esta página NO aparece el texto "INVOICE NUMBER", devuelve null o "CONTINUACION".

        2. TABLA DE ITEMS:
           - Busca la tabla principal de productos.
           - Mapeo de columnas obligatorio:
             'Code' o 'Material' -> modelo
             'Description' -> descripcion
             'Qty' o 'Quantity' -> cantidad (número entero)
             'Unit Value' o 'Unit Price' -> precio_unitario (decimal)
             'Origin', 'Orig', 'Ctry' -> origen
           
           - SOBRE EL ORIGEN:
             Busca explícitamente una columna llamada "Origin", "Orig" o "Ctry".
             El valor suele ser "Brazil", "BR", "China", "US", etc.
             SI NO ENCUENTRAS EL DATO DE ORIGEN EN LA FILA, DÉJALO COMO CADENA VACÍA "".
             NO INVENTES EL ORIGEN.

           - MANEJO DE SALTOS DE LÍNEA:
             Si la descripción o los datos se dividen en dos líneas visuales para un mismo producto, únelos en un solo objeto JSON.

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
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "origen": "..."
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
            # CAMBIO AQUI: Usamos el modelo 11B que es la versión estable actual
            model="llama-3.2-11b-vision-preview", 
            temperature=0.0, 
            max_tokens=4096,
            stream=False,
            response_format={"type": "json_object"}, 
        )
        texto_respuesta = chat_completion.choices[0].message.content
        return json.loads(texto_respuesta), None
    except Exception as e:
        if "model_decommissioned" in str(e):
             return {}, "⚠️ El modelo IA ha cambiado. Actualiza el nombre del modelo en el código."
        return {}, f"Error Groq: {str(e)}"

# ==========================================
# ⚙️ PROCESAMIENTO
# ==========================================
def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        # Convertimos PDF a imágenes
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
            
            # Lógica de continuidad de factura
            if not factura_actual or factura_actual.lower() in ["none", "null", "continuacion", "pendiente"] or len(factura_actual) < 3:
                factura_id = ultimo_numero_factura
            else:
                factura_id = factura_actual
                ultimo_numero_factura = factura_actual

            # Guardamos Items
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    # Agregar metadatos
                    item["Factura_Origen"] = factura_id
                    
                    # Asegurar campos
                    if "origen" not in item or item["origen"] is None: 
                        item["origen"] = "" 
                    if "modelo" not in item: item["modelo"] = ""
                    if "descripcion" not in item: item["descripcion"] = ""
                    if "cantidad" not in item: item["cantidad"] = 0
                    if "precio_unitario" not in item: item["precio_unitario"] = 0.0
                    
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
        time.sleep(0.2) 

    my_bar.empty()
    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.info("💡 Modelo Llama 3.2 11B Vision Activo")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar con Groq"):
    gran_acumulado = []
    st.divider()
    
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 Procesando: {uploaded_file.name}", expanded=True):
            with st.spinner(f"Leyendo documento..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path) 
                
                if items:
                    st.success(f"✅ {len(items)} items extraídos.")
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos extraíbles.")

    # --- GENERACIÓN DEL EXCEL FINAL ---
    if gran_acumulado:
        st.divider()
        st.subheader("📥 Zona de Descargas")
        
        # 1. Crear DataFrame
        df_final = pd.DataFrame(gran_acumulado)
        
        # 2. Ordenar Columnas
        cols_deseadas = ['modelo', 'descripcion', 'cantidad', 'precio_unitario', 'origen', 'Factura_Origen']
        cols_existentes = [c for c in cols_deseadas if c in df_final.columns]
        df_export = df_final[cols_existentes]
        
        st.dataframe(df_export, use_container_width=True)
        
        # 3. Generar Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_export.to_excel(writer, index=False, sheet_name='Detalle_Items')
            
            workbook = writer.book
            worksheet = writer.sheets['Detalle_Items']
            format_text = workbook.add_format({'text_wrap': False})
            
            worksheet.set_column('A:A', 15)
            worksheet.set_column('B:B', 50)
            worksheet.set_column('C:C', 10)
            worksheet.set_column('D:D', 12)
            worksheet.set_column('E:E', 15)
            worksheet.set_column('F:F', 20)
        
        # 4. Botón descarga
        st.download_button(
            label="📊 Descargar Excel Normal (.xlsx)",
            data=buffer.getvalue(),
            file_name="Reporte_Extraido.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
