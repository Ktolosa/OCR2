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

        Responde SOLAMENTE con un JSON válido con esta estructura:
        {
            "tipo_documento": "Original/Copia",
            "numero_factura": "Invoice #",
            "fecha": "YYYY-MM-DD",
            "orden_compra": "PO #",
            "proveedor": "Vendor Name",
            "cliente": "Sold To",
            "items": [
                {
                    "modelo": "Model No",
                    "descripcion": "Description",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,
    "Factura RadioShack": """
        Analiza esta factura de RadioShack.
        Extrae los datos en JSON. Usa SKU como modelo.
        Estructura JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "...",
            "fecha": "...",
            "proveedor": "RadioShack",
            "cliente": "...",
            "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}],
            "total_factura": 0.0
        }
    """,
    "Factura Mabe": """
        Analiza esta factura de Mabe.
        Extrae los datos en JSON. Usa CODIGO MABE como modelo. Ignora impuestos.
        Estructura JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "...",
            "fecha": "...",
            "proveedor": "Mabe",
            "cliente": "...",
            "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}],
            "total_factura": 0.0
        }
    """
}

# ==========================================
# 🛠️ FUNCIONES AUXILIARES (IMAGEN A BASE64)
# ==========================================
def codificar_imagen(image):
    """Convierte una imagen PIL a string Base64 para enviarla a Groq."""
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS CON GROQ
# ==========================================
def analizar_pagina(image, prompt_sistema):
    try:
        # 1. Preparar imagen
        base64_image = codificar_imagen(image)
        
        # 2. Llamada a la API de Groq (MODELO ACTUALIZADO A LLAMA 4 SCOUT)
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_sistema},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                            },
                        },
                    ],
                }
            ],
            # --- AQUÍ ESTÁ EL CAMBIO IMPORTANTE ---
            model="meta-llama/llama-4-scout-17b-16e-instruct", 
            # --------------------------------------
            temperature=0.1,
            max_tokens=4096, # Aumentado para respuestas largas
            top_p=1,
            stream=False,
            response_format={"type": "json_object"}, 
        )

        # 3. Obtener respuesta
        texto_respuesta = chat_completion.choices[0].message.content

        # 4. Convertir a Diccionario Python
        return json.loads(texto_respuesta), None

    except Exception as e:
        # Capturamos error para mostrarlo claro si vuelve a cambiar el modelo
        error_msg = str(e)
        if "model_decommissioned" in error_msg:
            return {}, "⚠️ El modelo de IA ha cambiado. Revisa la documentación de Groq."
        return {}, f"Error Groq: {error_msg}"

# ==========================================
# ⚙️ PROCESAMIENTO DE PDF
# ==========================================
def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF: {e}"

    items_locales = []
    resumen_local = []
    
    # Barra de progreso
    my_bar = st.progress(0, text=f"Analizando {filename}...")

    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
        
        # Filtro de Copias (Lógica Python)
        elif not data or "copia" in str(data.get("tipo_documento", "")).lower():
            # Es copia, la ignoramos pero no mostramos error
            pass
        else:
            # Es Original
            factura_id = data.get("numero_factura", "S/N")
            
            # Guardamos items
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Archivo_Origen"] = filename
                    item["Factura_Origen"] = factura_id
                    items_locales.append(item)
            
            # Guardamos resumen
            resumen_local.append({
                "Archivo": filename,
                "Factura": factura_id,
                "Total": data.get("total_factura"),
                "Cliente": data.get("cliente")
            })
        
        # Actualizar barra
        my_bar.progress((i + 1) / len(images))
        time.sleep(0.5) 

    my_bar.empty()
    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ DE USUARIO
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
