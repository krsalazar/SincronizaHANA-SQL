import pyodbc
from hdbcli import dbapi
import streamlit as st
import os
from dotenv import load_dotenv

load_dotenv()

def normalizar_estatus(estatus):
    if estatus is None:
        return 'ACTIVO'
    
    estatus_str = str(estatus).strip().upper()
    if estatus_str == '' or estatus_str == 'ACTIVO':
        return 'ACTIVO'
        
    return estatus_str

def transferir_datos(proceso, empresa_id, empresa_nombre, empresa_nit, fecha_inicio, fecha_fin):
    # Configuración de HANA (Compartida por todos los procesos)
    hana_host = os.getenv('HANA_HOST')
    hana_port = 30015 
    hana_user = os.getenv('HANA_USER')
    hana_password = os.getenv('HANA_PASS')

    try:
        # ---------------------------------------------------------
        # PROCESO 3: EJECUTA SP EN HANA
        # ---------------------------------------------------------
        if proceso == "Sincronizar SAP":
            hana_conn = dbapi.connect(address=hana_host, port=hana_port, user=hana_user, password=hana_password)
            hana_cursor = hana_conn.cursor()
            
            # Ejecución del Procedimiento Almacenado
            consulta_sp = 'CALL "REPORTES"."DARA_INSERTA_DOCUMENTO_SISTEMAS_SAP_TEST"(?, ?, ?)'
            hana_cursor.execute(consulta_sp, (empresa_nit, fecha_inicio, fecha_fin))
            hana_conn.commit()
            
            return f"Sincronización ejecutada correctamente para el NIT {empresa_nit} ({empresa_nombre})."

        # ---------------------------------------------------------
        # PROCESOS 1 Y 2: EXTRACCIÓN DE SQL SERVER Y CARGA A HANA
        # ---------------------------------------------------------
        sql_server = os.getenv('SQL_SERVER')
        sql_user = os.getenv('SQL_USER')
        sql_pass = os.getenv('SQL_PASS')
        
        if proceso == "Sincronizar Convenia":
            sql_db = os.getenv('SQL_DB_CONVENIA')
        else:
            sql_db = os.getenv('SQL_DB_POS') # GDA_FEL
            
        sql_conn_str = (
            f'DRIVER={{ODBC Driver 18 for SQL Server}};'
            f'SERVER={sql_server};'
            f'DATABASE={sql_db};'
            f'UID={sql_user};'
            f'PWD={sql_pass};'
            f'TrustServerCertificate=yes;'
        )
        
        sql_conn = pyodbc.connect(sql_conn_str)
        sql_cursor = sql_conn.cursor()

        if proceso == "Sincronizar Convenia":
            consulta_extraccion = """
            WITH ResumenDetalle AS (
                SELECT 
                    T1.DocumentoID,
                    SUM(CASE WHEN T2.Servicio = 1 THEN T1.Subtotal-T1.IVAdetalle-T1.INGUATdetalle ELSE 0 END) AS SERVICIOS,
                    SUM(CASE WHEN T2.Servicio = 0 THEN T1.Subtotal-T1.IVAdetalle-T1.INGUATdetalle ELSE 0 END) AS OTROS_PRODUCTOS
                FROM DocumentosDetalles T1
                INNER JOIN OITM_Articulos T2 ON T1.ItemCode = T2.ItemCode
                GROUP BY T1.DocumentoID
            )
            SELECT 
                T0.DocumentoID,
                'CONVENIA',
                ?,
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
                ?,
                CASE WHEN T0.UltimoEstadoBitacora = 8 THEN 'ANULADO' ELSE 'ACTIVO' END
            FROM Documentos T0
            LEFT JOIN ResumenDetalle RD ON T0.DocumentoID = RD.DocumentoID
            WHERE EmpresaID = ?
            AND CONVERT(DATE, FechaEmision) >= ? AND CONVERT(DATE, FechaEmision) <= ?
            """
        else: # Proceso POS GDA
            consulta_extraccion = """
            WITH ResumenDetalle AS (
                SELECT 
                    T1.IdDocumento,
                    SUM(CASE WHEN T1.TipoProducto = 1 THEN T1.Subtotal-T1.IVAdetalle-T1.IDPdetalle ELSE 0 END) AS COMBUSTIBLES,
                    SUM(CASE WHEN T1.TipoProducto = 0 THEN T1.Subtotal-T1.IVAdetalle-T1.IDPdetalle ELSE 0 END) AS SERVICIOS,
                    SUM(CASE WHEN T1.TipoProducto = 2 THEN T1.Subtotal-T1.IVAdetalle-T1.IDPdetalle ELSE 0 END) AS OTROS_PRODUCTOS
                FROM DocumentoDetalles T1
                GROUP BY T1.IdDocumento
            )
            SELECT 
                T0.IdDocumento,
                'POS',
                ?, 
                T0.IdReceptor,
                T0.UUID,
                T0.Serie,
                T0.Numero,
                T0.Monto,
                T0.IVA,
                T0.IDP,
                T0.FechaEmision,
                T0.TipoDTE,
                ISNULL(T0.CentroCostoID,'0001'),
                T0.NumeroEstablecimientoSAT,
                ISNULL(RD.SERVICIOS, 0),
                ISNULL(RD.OTROS_PRODUCTOS, 0),
                RD.COMBUSTIBLES,
                T0.CorteID,
                ?, 
                CASE WHEN T0.UltimoEstado_Bitacora = 8 THEN 'ANULADO' ELSE 'ACTIVO' END
            FROM Documento T0
            LEFT JOIN ResumenDetalle RD ON T0.IdDocumento = RD.IdDocumento
            WHERE EmpresaID = ?
            AND CONVERT(DATE, FechaEmision) >= ? AND CONVERT(DATE, FechaEmision) <= ?
            """
        
        parametros = (empresa_nombre, empresa_nit, empresa_id, fecha_inicio, fecha_fin)
        sql_cursor.execute(consulta_extraccion, parametros)
        
        datos_sql = sql_cursor.fetchall()
        
        if not datos_sql:
            return "No hay documentos pendientes de procesar en las fechas seleccionadas."

        hana_conn = dbapi.connect(address=hana_host, port=hana_port, user=hana_user, password=hana_password)
        hana_cursor = hana_conn.cursor()

        uuids_sql = [fila[4] for fila in datos_sql]
        diccionario_hana = {}
        tamano_lote = 1000
        
        for i in range(0, len(uuids_sql), tamano_lote):
            lote_uuids = uuids_sql[i:i + tamano_lote]
            placeholders = ', '.join(['?'] * len(lote_uuids))
            
            consulta_hana_existentes = f"SELECT UUID, ESTATUS FROM SBO_DW_FEL.DOCUMENTOS WHERE UUID IN ({placeholders})"
            hana_cursor.execute(consulta_hana_existentes, lote_uuids)
            
            for fila_hana in hana_cursor.fetchall():
                diccionario_hana[fila_hana[0]] = fila_hana[1]
                
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
        
        resumen = f"Proceso completado con éxito. Extracción SQL: {len(datos_sql)} | "
        if lista_insertar or lista_actualizar:
            resumen += f"Insertados: {len(lista_insertar)} | Actualizados: {len(lista_actualizar)}"
        else:
            resumen += "Todos los documentos ya estaban en HANA y sin cambios."
            
        return resumen

    except Exception as e:
        if 'hana_conn' in locals(): hana_conn.rollback()
        return f"Ocurrió un error crítico: {e}"

    finally:
        # Solo se cierran si fueron inicializados
        if 'sql_cursor' in locals(): sql_cursor.close()
        if 'sql_conn' in locals(): sql_conn.close()
        if 'hana_cursor' in locals(): hana_cursor.close()
        if 'hana_conn' in locals(): hana_conn.close()


def interfaz_web():
    st.set_page_config(page_title="Sincronizador FEL a HANA", layout="centered")
    
    # Menú lateral ampliado
    st.sidebar.title("Navegación")
    proceso_seleccionado = st.sidebar.radio(
        "Selecciona el proceso a ejecutar:", 
        ["Sincronizar Convenia", "Sincronizar POS GDA", "Sincronizar SAP"]
    )
    
    st.title(proceso_seleccionado)
    
    # Diccionarios base
    empresas_convenia = {
        2: {"nombre": "TIENDAS EN RUTA S.A", "nit": "114909229"},
        3: {"nombre": "MARIO ROLANDO RODRIGUEZ POSADAS (Convenia)", "nit": "4880358"},
        4: {"nombre": "CONSULTORA CRECE, SOCIEDAD ANÓNIMA", "nit": "30730104"},
        5: {"nombre": "INVERSIONES CIUDAD VIEJA, S.A.", "nit": "11874937"}
    }
    
    empresas_pos = {
        58: {"nombre": "COMBUSTIBLES ENERGETICOS DE GUATEMALA, SOCIEDAD ANONIMA", "nit": "111890357"},
        72: {"nombre": "MIT COMPANY, SOCIEDAD ANÓNIMA", "nit": "112052681"},
        80: {"nombre": "MARIO ROLANDO RODRIGUEZ POSADAS (POS)", "nit": "4880358"}
    }

    # Asignación de catálogos dependiendo del proceso
    if proceso_seleccionado == "Sincronizar Convenia":
        empresas_catalogo = empresas_convenia
    elif proceso_seleccionado == "Sincronizar POS GDA":
        empresas_catalogo = empresas_pos
    else:
        # Sincronizar SAP consolida ambas bases uniendo ambos diccionarios
        empresas_catalogo = {**empresas_convenia, **empresas_pos}

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
        
        with st.spinner(f"Ejecutando proceso para {empresa_nombre}..."):
            resultado = transferir_datos(
                proceso_seleccionado,
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