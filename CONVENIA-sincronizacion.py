import pyodbc
from hdbcli import dbapi
import streamlit as st
import os
from dotenv import load_dotenv

# Esto lee el archivo .env y carga las variables a la memoria
load_dotenv()

def transferir_datos(empresa_id, empresa_nombre, empresa_nit, fecha_inicio, fecha_fin):
    # 1. Configuración de conexión a SQL Server
    sql_server = os.getenv('SQL_SERVER')
    sql_db = os.getenv('SQL_DB')
    sql_user = os.getenv('SQL_USER')
    sql_pass = os.getenv('SQL_PASS')
    
    # Se usa una f-string (nota las llaves dobles {{ }} en el DRIVER para escapar el formato)
    sql_conn_str = (
            f'DRIVER={{ODBC Driver 18 for SQL Server}};'  # Cambio al driver 18
            f'SERVER={sql_server};'
            f'DATABASE={sql_db};'
            f'UID={sql_user};'
            f'PWD={sql_pass};'
            f'TrustServerCertificate=yes;' # Obligatorio en v18 si tu SQL no tiene certificado SSL firmado
        )
    
    # 2. Configuración de conexión a SAP HANA
    hana_host = os.getenv('HANA_HOST')
    hana_port = 30015 
    hana_user = os.getenv('HANA_USER')
    hana_password = os.getenv('HANA_PASS')
    try:
        print("Conectando a SQL Server...") 
        sql_conn = pyodbc.connect(sql_conn_str) 
        sql_cursor = sql_conn.cursor() 

        # Consulta parametrizada con '?'
        consulta_extraccion = """
       WITH ResumenDetalle AS (
            SELECT 
                T1.DocumentoID,
                SUM(CASE WHEN T2.Servicio = 1 THEN T1.Subtotal-T1.IVAdetalle-T1.INGUATdetalle ELSE 0 END) AS SERVICIOS,
                SUM(CASE WHEN T2.Servicio = 0 THEN T1.Subtotal-T1.IVAdetalle-T1.INGUATdetalle ELSE 0 END) AS OTROS_PRODUCTOS
            FROM DocumentosDetalles T1
            INNER JOIN OITM_Articulos T2 
                ON T1.ItemCode = T2.ItemCode
            GROUP BY T1.DocumentoID
        )
            SELECT 
                T0.DocumentoID,
                'CONVENIA',
                ?, -- [Reemplazo] Nombre dinámico de la empresa
                T0.IdReceptor,
                T0.UUID,
                T0.Serie,
                T0.Numero,
                T0.Monto*T0.TipoCambio,
                T0.IVA*T0.TipoCambio,
                T0.INGUAT*T0.TipoCambio,
                T0.FechaEmision,
                T0.TipoDTE,
                ISNULL(T0.CentroCostoID,'0001'),
                T0.NumeroEstablecimientoSAT,
                ISNULL(RD.SERVICIOS, 0)*T0.TipoCambio,
                ISNULL(RD.OTROS_PRODUCTOS, 0)*T0.TipoCambio,
                0,
                T0.CorteID,
                ?, -- [Reemplazo] NIT dinámico
                CASE 
                    WHEN T0.UltimoEstadoBitacora = 8 THEN 'ANULADO' 
                    ELSE 'ACTIVO' 
                END
            FROM Documentos T0
            LEFT JOIN ResumenDetalle RD ON T0.DocumentoID = RD.DocumentoID
            WHERE EmpresaID = ? -- [Reemplazo] ID de Empresa dinámico
            AND CONVERT(DATE, FechaEmision) >= ? AND CONVERT(DATE, FechaEmision) <= ? -- [Reemplazo] Fechas dinámicas
        """

        # Pasamos las variables a pyodbc en el orden exacto de los '?'
        parametros = (empresa_nombre, empresa_nit, empresa_id, fecha_inicio, fecha_fin)
        sql_cursor.execute(consulta_extraccion, parametros)
        
        datos_sql = sql_cursor.fetchall() 
        print(f"Se extrajeron {len(datos_sql)} registros de SQL Server.") 

        if not datos_sql: 
            return "No hay datos nuevos para procesar."

       
        return f"Proceso completado. Registros extraídos: {len(datos_sql)}."

    except Exception as e: 
        return f"Ocurrió un error crítico: {e}" 
    
    finally: 
        if 'sql_cursor' in locals(): sql_cursor.close() 
        if 'sql_conn' in locals(): sql_conn.close() 
        if 'hana_cursor' in locals(): hana_cursor.close() 
        if 'hana_conn' in locals(): hana_conn.close() 

def interfaz_web():
    st.set_page_config(page_title="Sincronizador FEL a HANA", layout="centered")
    st.title("Sincronización de Documentos FEL")
    
    # Diccionario basado en los comentarios de tu script
    empresas_catalogo = {
        2: {"nombre": "TIENDAS EN RUTA S.A", "nit": "114909229"},
        3: {"nombre": "MARIO ROLANDO RODRIGUEZ POSADAS", "nit": "4880358"},
        4: {"nombre": "CONSULTORA CRECE, SOCIEDAD ANÓNIMA", "nit": "30730104"},
        5: {"nombre": "INVERSIONES CIUDAD VIEJA, S.A.", "nit": "11874937"}
    }

    # Creación del formulario de entrada
    with st.form("form_parametros"):
        # Mapeo de opciones para el Dropdown (SelectBox)
        opciones = {f"{k} - {v['nombre']}": k for k, v in empresas_catalogo.items()}
        seleccion_empresa = st.selectbox("Seleccione la Empresa:", list(opciones.keys()))
        
        col1, col2 = st.columns(2)
        fecha_ini = col1.date_input("Fecha Inicio")
        fecha_fin = col2.date_input("Fecha Fin")
        
        btn_ejecutar = st.form_submit_button("Ejecutar Sincronización")

    # Acción al presionar el botón
    if btn_ejecutar:
        empresa_id = opciones[seleccion_empresa]
        empresa_nombre = empresas_catalogo[empresa_id]["nombre"]
        empresa_nit = empresas_catalogo[empresa_id]["nit"]
        
        with st.spinner(f"Extrayendo datos de SQL Server para {empresa_nombre}..."):
            # Las fechas se formatean a string para que SQL Server las interprete correctamente
            resultado = transferir_datos(
                empresa_id, 
                empresa_nombre, 
                empresa_nit, 
                fecha_ini.strftime('%Y-%m-%d'), 
                fecha_fin.strftime('%Y-%m-%d')
            )
            
            if "error" in resultado.lower():
                st.error(resultado)
            else:
                st.success(resultado)

if __name__ == "__main__":
    interfaz_web()