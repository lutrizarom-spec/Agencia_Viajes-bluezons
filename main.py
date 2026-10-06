from conectar import crear_conexion  # Importa la función que abre la base local y habilita las claves foráneas.
from dao.paquete_dao import PaqueteDao  # Importa el DAO que crea, inserta, consulta y elimina registros de paquetes.
from model.paquete_crucero import Paquete_Crucero  # Importa el modelo crucero para registrar y mostrar sus paquetes.
from model.paquete_internacional import Paquete_Internacional  # Importa el modelo internacional para registrar y mostrar sus paquetes.
from model.paquete_nacional import Paquete_Nacional  # Importa el modelo nacional para registrar y mostrar sus paquetes.
from model.paquete_turistico import Paquete_Turistico  # Importa el modelo base usado para tipar los paquetes reconstruidos.


def leer_entero(mensaje: str) -> int:  # Define un lector que solicita un entero y repite la pregunta ante una entrada inválida.
    while True:  # Mantiene la solicitud activa hasta que se ingrese un valor entero válido.
        try:  # Permite interceptar el error que ocurre cuando el texto no representa un entero.
            return int(input(mensaje))  # Lee la entrada, la convierte a entero y la devuelve a quien la solicitó.
        except ValueError:  # Maneja exclusivamente el caso de texto que no puede convertirse a entero.
            print("Entrada inválida: ingrese un número entero.")  # Informa al usuario el formato esperado antes de volver a preguntar.


def leer_decimal(mensaje: str) -> float:  # Define un lector que solicita un número decimal y reintenta si la entrada no es válida.
    while True:  # Repite la solicitud hasta poder convertir correctamente el texto a número decimal.
        try:  # Agrupa la conversión para manejar una entrada con formato no numérico.
            return float(input(mensaje))  # Lee el texto, lo convierte a decimal y lo devuelve para usarlo en el paquete.
        except ValueError:  # Captura el error de conversión sin ocultar otros tipos de fallos.
            print("Entrada inválida: ingrese un número.")  # Explica que se necesita un valor numérico e inicia otro intento.


def registrar_paquete(paquete_dao: PaqueteDao) -> None:  # Define el flujo de consola para capturar y persistir un paquete de cualquier subtipo admitido.
    print("Tipo de paquete: 1. Nacional  2. Internacional  3. Crucero")  # Muestra las categorías disponibles para que el usuario elija una.
    tipo = leer_entero("Seleccione el tipo: ")  # Solicita y valida como entero la opción de tipo seleccionada.
    if tipo not in (1, 2, 3):  # Verifica que la selección corresponda a una de las tres categorías disponibles.
        print("Tipo de paquete inválido.")  # Informa que no se puede registrar una categoría no contemplada.
        return  # Termina el registro actual sin guardar datos incompletos o de tipo desconocido.
    codigo = leer_entero("Código: ")  # Captura el identificador entero que distinguirá al paquete en la tabla.
    nombre = input("Nombre: ").strip()  # Captura el nombre y elimina espacios sobrantes al principio y al final.
    duracion = leer_entero("Duración en días: ")  # Captura la cantidad de días como un valor entero.
    precio_base = leer_decimal("Precio base: ")  # Captura el precio inicial como un valor decimal.
    try:  # Mantiene la aplicación de consola activa cuando el modelo rechaza datos fuera del dominio.
        paquete: Paquete_Turistico  # Declara que la variable contendrá una instancia de la clase base o de uno de sus subtipos.
        if tipo == 1:  # Selecciona el constructor nacional cuando el usuario eligió la primera categoría.
            paquete = Paquete_Nacional(codigo, nombre, duracion, precio_base)  # Crea el paquete nacional con los datos comunes ingresados.
        elif tipo == 2:  # Selecciona el constructor internacional cuando el usuario eligió la segunda categoría.
            pasaporte_respuesta = leer_entero("¿Pasaporte válido? (1: Sí, 0: No): ")  # Solicita el estado del pasaporte mediante una opción numérica simple.
            while pasaporte_respuesta not in (0, 1):  # Valida que el estado se exprese únicamente con 0 o 1.
                print("Opción inválida: escriba 1 para Sí o 0 para No.")  # Informa el rango esperado cuando el valor no representa una respuesta válida.
                pasaporte_respuesta = leer_entero("¿Pasaporte válido? (1: Sí, 0: No): ")  # Solicita de nuevo el estado hasta obtener una opción permitida.
            paquete = Paquete_Internacional(codigo, nombre, duracion, precio_base, bool(pasaporte_respuesta))  # Crea el objeto internacional y convierte la opción a booleano.
        else:  # Maneja la única categoría restante, que corresponde a los cruceros.
            impuesto_puerto = leer_decimal("Impuesto portuario: ")  # Captura el impuesto fijo que se sumará al precio del crucero.
            paquete = Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto_puerto)  # Crea el crucero con sus datos comunes y su impuesto específico.
        paquete_dao.insertar_paquete(paquete)  # Persiste el paquete creado mediante el DAO y su inserción parametrizada.
    except ValueError as error:  # Captura solo los errores de validación del dominio y no oculta fallos de base de datos.
        print(f"No se pudo registrar el paquete: {error}")  # Explica el dato rechazado y permite que el menú continúe.
        return  # Termina este intento sin insertar un paquete inválido.
    print(f"Paquete '{paquete.nombre}' registrado correctamente.")  # Confirma el nombre canónico y el registro exitoso.


def mostrar_paquetes(paquete_dao: PaqueteDao) -> None:  # Define la consulta y presentación de los paquetes almacenados.
    filas = paquete_dao.obtener_paquetes()  # Recupera del DAO todas las filas de paquetes en el formato de tuplas SQLite.
    if not filas:  # Comprueba si la consulta no devolvió registros para evitar mostrar un listado vacío sin explicación.
        print("No hay paquetes registrados.")  # Informa claramente que todavía no existen paquetes guardados.
        return  # Finaliza la operación de listado porque no hay datos que presentar.
    for fila in filas:  # Recorre cada fila obtenida para reconstruir el modelo y mostrar sus detalles.
        codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto = fila  # Desempaqueta las columnas según el orden definido en PaqueteDao.
        paquete: Paquete_Turistico  # Declara el tipo común del objeto que será instanciado según su categoría.
        if tipo == "nacional":  # Comprueba si la fila corresponde al modelo nacional.
            paquete = Paquete_Nacional(codigo, nombre, duracion, precio_base)  # Reconstruye el objeto nacional con sus campos compartidos.
        elif tipo == "internacional":  # Comprueba si la fila corresponde al modelo internacional.
            paquete = Paquete_Internacional(codigo, nombre, duracion, precio_base, bool(pasaporte_valido))  # Reconstruye el objeto y restaura el entero SQLite como booleano.
        elif tipo == "crucero":  # Comprueba si la fila corresponde al modelo de crucero.
            paquete = Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto_puerto)  # Reconstruye el crucero con su impuesto portuario almacenado.
        else:  # Atiende filas genéricas o categorías no contempladas por los modelos concretos actuales.
            paquete = Paquete_Turistico(codigo, nombre, duracion, precio_base)  # Reconstruye el modelo base para presentar el registro sin atributos específicos.
        print(paquete.obtener_detalle())  # Muestra la descripción común proporcionada por el objeto del modelo.
        print(f"Precio final: ${paquete.calcular_precio():.2f}")  # Calcula y muestra el precio según la regla polimórfica del subtipo.
        if isinstance(paquete, Paquete_Internacional):  # Comprueba si el objeto tiene el atributo adicional de validez del pasaporte.
            estado_pasaporte = "Sí" if paquete.pasaporte_valido else "No"  # Convierte el booleano en una etiqueta legible para la consola.
            print(f"Pasaporte válido: {estado_pasaporte}")  # Muestra el estado del pasaporte asociado al paquete internacional.
        elif isinstance(paquete, Paquete_Crucero):  # Comprueba si el objeto tiene el atributo adicional de impuesto portuario.
            print(f"Impuesto portuario: ${paquete.impuesto_puerto:.2f}")  # Muestra el impuesto que forma parte del precio final del crucero.
        print("-" * 50)  # Separa visualmente los datos de un paquete de los del siguiente.


def main() -> None:  # Define el punto principal de ejecución de la aplicación de consola.
    conexion = crear_conexion()  # Abre la base agencia.db con las claves foráneas habilitadas.
    paquete_dao = PaqueteDao(conexion)  # Construye el DAO que reutilizará la conexión y su cursor.
    try:  # Asegura que la conexión se cierre al salir del menú incluso si ocurre un error en una operación.
        paquete_dao.crear_tabla()  # Garantiza que la tabla exista para que las opciones de datos funcionen desde el primer inicio.
        while True:  # Mantiene el menú activo hasta que se seleccione la opción de salida.
            print("\n--- Agencia de Viajes ---")  # Presenta el encabezado del menú en cada iteración.
            print("1. Crear tabla")  # Muestra la opción para crear o asegurar la tabla de paquetes.
            print("2. Registrar paquete (Nacional, Internacional o Crucero)")  # Muestra la opción para capturar uno de los subtipos disponibles.
            print("3. Listar paquetes y ver detalles/precios")  # Muestra la opción para consultar y presentar los paquetes registrados.
            print("4. Dar de baja paquete por código")  # Aclara que se ocultará del catálogo sin borrar su historial.
            print("5. Salir")  # Muestra la opción que termina el programa.
            opcion = leer_entero("Seleccione una opción: ")  # Solicita y convierte a entero la acción elegida en el menú.
            if opcion == 1:  # Dirige la ejecución a la creación idempotente de la tabla.
                paquete_dao.crear_tabla()  # Crea la tabla si falta y confirma el cambio desde el DAO.
                print("La tabla de paquetes está lista.")  # Informa que la tabla existe o acaba de ser creada.
            elif opcion == 2:  # Dirige la ejecución al flujo de captura de un nuevo paquete.
                registrar_paquete(paquete_dao)  # Solicita los datos del tipo elegido y los guarda en la base.
            elif opcion == 3:  # Dirige la ejecución a la consulta de todos los paquetes guardados.
                mostrar_paquetes(paquete_dao)  # Presenta detalles y precio calculado para cada registro.
            elif opcion == 4:  # Dirige la ejecución al flujo de baja lógica de un registro existente.
                codigo = leer_entero("Código del paquete que desea dar de baja: ")  # Captura el código del paquete que se ocultará del catálogo.
                desactivados = paquete_dao.eliminar_paquete(codigo)  # Cambia activo a cero y conserva las referencias históricas.
                if desactivados:  # Comprueba si la base tenía el paquete publicado.
                    print("Paquete dado de baja; su historial se conservó.")  # Confirma que dejó de venderse sin borrar las reservas.
                else:  # Maneja código inexistente o que ya estaba inactivo.
                    print("No se encontró un paquete activo con ese código.")  # Informa que no hubo una baja nueva que realizar.
            elif opcion == 5:  # Reconoce la opción de salida y termina el ciclo del menú.
                print("Hasta luego.")  # Muestra un mensaje de despedida antes de liberar los recursos.
                break  # Sale del bucle y continúa al bloque que cierra la conexión.
            else:  # Maneja cualquier número que no corresponda a una opción disponible.
                print("Opción inválida. Seleccione un número del 1 al 5.")  # Indica el rango aceptado y permite volver a mostrar el menú.
    finally:  # Ejecuta el cierre tanto en una salida normal como si una operación genera una excepción.
        conexion.close()  # Libera la conexión SQLite y los recursos asociados al finalizar la aplicación.


if __name__ == "__main__":  # Comprueba que el archivo se ejecute directamente y no se importe desde otro módulo.
    main()  # Inicia el programa interactivo solo al ejecutar main.py como aplicación.
