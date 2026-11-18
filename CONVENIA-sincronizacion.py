import pyodbc 
from hdbcli import dbapi 
import streamlit as st
import os
from dotenv import load_dotenv

# Cargar las variables de entorno desde el archivo .env
load_dotenv()

def normalizar_estatus(estatus): 
    """
    Normaliza el estatus para que NULL, vacío ('') o 'ACTIVO' sean tratados 
    como equivalentes ('ACTIVO'). Cualquier otro ('ANULADO') se mantiene.
    """ 
    if estatus is None: 
        return 'ACTIVO' 
    
    estatus_str = str(estatus).strip().upper() 
    if estatus_str == '' or estatus_str == 'ACTIVO': 
        return 'ACTIVO' 
        
    return estatus_str 

def transferir_datos(empresa_id, empresa_nombre, empresa_nit, fecha_inicio, fecha_fin):
    # 1. Configuración de conexión a SQL Server desde .env
    sql_server = os.getenv('SQL_SERVER')
    sql_db = os.getenv('SQL_DB')
    sql_user = os.getenv('SQL_USER')
    sql_pass = os.getenv('SQL_PASS')
    
    # Cadena actualizada para ODBC Driver 18 con TrustServerCertificate
    sql_conn_str = (
        f'DRIVER={{ODBC Driver 18 for SQL Server}};'
        f'SERVER={sql_server};'
        f'DATABASE={sql_db};'
        f'UID={sql_user};'
        f'PWD={sql_pass};'
        f'TrustServerCertificate=yes;'
    )
    
    # 2. Configuración de conexión a SAP HANA desde .env
    hana_host = os.getenv('HANA_HOST')
    hana_port = 30015  
    hana_user = os.getenv('HANA_USER')
    hana_password = os.getenv('HANA_PASS')

    try:
        # --- 1. EXTRACCIÓN (SQL Server) --- 
        sql_conn = pyodbc.connect(sql_conn_str) 
        sql_cursor = sql_conn.cursor() 

        # Consulta con parámetros dinámicos (?)
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
                ?, -- [Dinámico] Empresa
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
                ?, -- [Dinámico] NIT
                CASE 
                    WHEN T0.UltimoEstadoBitacora = 8 THEN 'ANULADO' 
                    ELSE 'ACTIVO' 
                END
            FROM Documentos T0
            LEFT JOIN ResumenDetalle RD ON T0.DocumentoID = RD.DocumentoID
            WHERE EmpresaID = ? -- [Dinámico] ID de Empresa
            AND CONVERT(DATE, FechaEmision) >= ? AND CONVERT(DATE, FechaEmision) <= ? -- [Dinámico] Fechas
        """
        
        parametros = (empresa_nombre, empresa_nit, empresa_id, fecha_inicio, fecha_fin)
        sql_cursor.execute(consulta_extraccion, parametros)
        
        datos_sql = sql_cursor.fetchall() 
        
        if not datos_sql: 
            return "No hay datos nuevos para procesar en el rango seleccionado."

        # --- 2. CONSULTA DE DOCUMENTOS EXISTENTES EN HANA --- 
        hana_conn = dbapi.connect(address=hana_host, port=hana_port, user=hana_user, password=hana_password) 
        hana_cursor = hana_conn.cursor() 

        # Extraemos solo los UUID (están en la posición 4 de la consulta SQL) 
        uuids_sql = [fila[4] for fila in datos_sql] 
        diccionario_hana = {} 
        tamano_lote = 1000  
        
        for i in range(0, len(uuids_sql), tamano_lote): 
            lote_uuids = uuids_sql[i:i + tamano_lote] 
            placeholders = ', '.join(['?'] * len(lote_uuids)) 
            
            consulta_hana_existentes = f"SELECT UUID, ESTATUS FROM SBO_DW_FEL.DOCUMENTOS WHERE UUID IN ({placeholders})" 
            hana_cursor.execute(consulta_hana_existentes, lote_uuids) 
            datos_hana_lote = hana_cursor.fetchall() 
            
            for fila_hana in datos_hana_lote: 
                diccionario_hana[fila_hana[0]] = fila_hana[1] 
                
        # --- 3. CLASIFICACIÓN (Insertar vs Actualizar) --- 
        lista_insertar = [] 
        lista_actualizar = [] 

        for fila in datos_sql: 
            uuid = fila[4] 
            estatus_sql = normalizar_estatus(fila[19]) 

            if uuid not in diccionario_hana: 
                lista_insertar.append(tuple(fila)) 
            else: 
                estatus_hana = normalizar_estatus(diccionario_hana[uuid]) 
                if estatus_sql != estatus_hana: 
                    fila_actualizar = tuple(fila) + (uuid,) 
                    lista_actualizar.append(fila_actualizar) 

        # --- 4. CARGA A SAP HANA --- 
        if lista_insertar: 
            consulta_insercion = """
            INSERT INTO SBO_DW_FEL.DOCUMENTOS (
                ID, SOURCE, EMPRESA, NIT, UUID, SERIE, NUMERO, MONTO, IVA, IDP, 
                FECHA, TIPO, ESTACION, ESTABLECIMIENTO, SERVICIOS, OTROS_PRODUCTOS, 
                COMBUSTIBLE, CORTE, NITEMISOR, ESTATUS
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """ 
            hana_cursor.executemany(consulta_insercion, lista_insertar) 

        if lista_actualizar: 
            consulta_actualizacion = """
            UPDATE SBO_DW_FEL.DOCUMENTOS SET 
                ID=?, SOURCE=?, EMPRESA=?, NIT=?, UUID=?, SERIE=?, NUMERO=?, MONTO=?, IVA=?, IDP=?, 
                FECHA=?, TIPO=?, ESTACION=?, ESTABLECIMIENTO=?, SERVICIOS=?, OTROS_PRODUCTOS=?, 
                COMBUSTIBLE=?, CORTE=?, NITEMISOR=?, ESTATUS=?
            WHERE UUID=?
            """ 
            hana_cursor.executemany(consulta_actualizacion, lista_actualizar) 

        hana_conn.commit() 
        
        # Resumen final para la interfaz
        resumen = f"Proceso completado con éxito. Extracción SQL: {len(datos_sql)} | "
        if lista_insertar or lista_actualizar:
            resumen += f"Insertados: {len(lista_insertar)} | Actualizados: {len(lista_actualizar)}"
        else:
            resumen += "Todos los registros ya estaban en HANA y sin cambios."
            
        return resumen

    except Exception as e: 
        if 'hana_conn' in locals(): hana_conn.rollback() 
        return f"Ocurrió un error crítico: {e}"

    finally: 
        if 'sql_cursor' in locals(): sql_cursor.close() 
        if 'sql_conn' in locals(): sql_conn.close() 
        if 'hana_cursor' in locals(): hana_cursor.close() 
        if 'hana_conn' in locals(): hana_conn.close() 


def interfaz_web():
    st.set_page_config(page_title="Sincronizador FEL a HANA", layout="centered")
    st.title("Sincronización de Documentos FEL")
    
    # Catálogo de empresas parametrizado
    empresas_catalogo = {
        2: {"nombre": "TIENDAS EN RUTA S.A", "nit": "114909229"},
        3: {"nombre": "MARIO ROLANDO RODRIGUEZ POSADAS", "nit": "4880358"},
        4: {"nombre": "CONSULTORA CRECE, SOCIEDAD ANÓNIMA", "nit": "30730104"},
        5: {"nombre": "INVERSIONES CIUDAD VIEJA, S.A.", "nit": "11874937"}
    }

    with st.form("form_parametros"):
        opciones = {f"{k} - {v['nombre']}": k for k, v in empresas_catalogo.items()}
        seleccion_empresa = st.selectbox("Seleccione la Empresa:", list(opciones.keys()))
        
        col1, col2 = st.columns(2)
        fecha_ini = col1.date_input("Fecha Inicio")
        fecha_fin = col2.date_input("Fecha Fin")
        
        btn_ejecutar = st.form_submit_button("Ejecutar Sincronización")

    if btn_ejecutar:
        empresa_id = opciones[seleccion_empresa]
        empresa_nombre = empresas_catalogo[empresa_id]["nombre"]
        empresa_nit = empresas_catalogo[empresa_id]["nit"]
        
        with st.spinner(f"Sincronizando {empresa_nombre}..."):
            resultado = transferir_datos(
                empresa_id, 
                empresa_nombre, 
                empresa_nit, 
                fecha_ini.strftime('%Y-%m-%d'), 
                fecha_fin.strftime('%Y-%m-%d')
            )
            
            if "error crítico" in resultado.lower():
                st.error(resultado)
            elif "No hay datos nuevos" in resultado:
                st.warning(resultado)
            else:
                st.success(resultado)

if __name__ == "__main__":
    interfaz_web()