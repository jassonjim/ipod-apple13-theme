# Tema Apple 1.3 para iPod 5.5 · paquete compartible

Este paquete distribuye un **parche delta**, no una copia del firmware de Apple. Cada persona obtiene el firmware por su cuenta, hace una lectura completa de su propio iPod y genera localmente el candidato. El paquete no contiene un volcado de tu iPod, tu número de serie, tu partición de música ni tu `Apple Media.cdr`.

El objetivo es el iPod con vídeo de quinta generación mejorado, conocido como **5.5 gen / iPod25**, con Apple 1.3 (6.3). The Apple Wiki identifica esa variante y enlaza el IPSW `iPod_25.1.3.ipsw`; la página registra SHA-1 `0f2dd984c5d56475bdf3c14b4bc7541b799f2c85` y tamaño 6.533.633 bytes: [Firmware/iPod](https://theapplewiki.com/wiki/Firmware/iPod#Late_2006_(%22Enhanced%22/5.5th_generation,_September_2006_(2006-09))). También advierte que algunas revisiones de hardware usan firmware incompatible. El parche no sirve para iPod classic 6G, iPod vídeo 5G normal ni otros iPod aunque compartan conector.

El flasheador es **solo para macOS**: usa `diskutil`, `ioreg` y dispositivos `/dev/r…`. Necesitas Python 3 y permisos de administrador para leer o escribir la partición de firmware. El paquete no funciona en Windows ni Linux sin una adaptación específica que no está incluida.

## Qué contiene

- `firmware-theme-002.patch.json`: 6.953 rangos delta; no es una imagen Apple completa.
- `shareable_patch.py`: valida la partición y crea/verifica un candidato local.
- `backup_shareable_device.py`: hace dos lecturas completas de 160 MiB, solo lectura.
- `flash_shareable_patch.py`: detecta dinámicamente un iPod compatible, desmonta el volumen, vuelve a leer, pide confirmación y escribe sectores completos con verificación.
- `checksums.txt`: hashes de los archivos del paquete.
- `LICENSE-CODE.txt`: licencia del código original de este paquete; no licencia los datos binarios de Apple.

El candidato de referencia cambia 124.977 bytes en 155 sectores físicos (317.440 bytes máximos de escritura). El sector de checksum OSOS `0x4000` se escribe el último. El paquete exige que los bytes antiguos de cada rango coincidan; ante cualquier modificación desconocida se detiene y no intenta fusionarla.

## Antes de compartir o usar

El binario Apple sigue siendo software propietario. El acuerdo de firmware de Apple restringe copiar, modificar y redistribuir el software, así que publica solo este delta, los scripts y la documentación; no publiques el IPSW, un volcado completo ni un candidato de 160 MiB. Revisa las condiciones aplicables en [Apple Software License Agreements](https://www.apple.com/legal/sla/) y [EFI Firmware Update License](https://www.apple.com/legal/sla/docs/EFIFirmwareUpdate.pdf). La legalidad concreta depende de tu jurisdicción.

El flasheador no está ligado a un número de serie ni a `disk5`: busca un único dispositivo USB con vendor `1452`, product `4617`, comprueba la partición `Apple_MDFW` de 160 MiB y valida los hashes nativos de OSOS, RSRC y AUPD. Si hay dos iPod conectados, una revisión de hardware diferente o un firmware ya modificado, se detiene.

## Obtener el firmware de referencia

El IPSW se usa para comprobar que se descargó la versión correcta y para restaurar el iPod con la herramienta oficial si alguna vez fuera necesario. No se flashea el IPSW directamente con este paquete.

```sh
curl --fail --location \
  --output iPod_25.1.3.ipsw \
  https://secure-appldnld.apple.com/iPod/SBML/osx/bundles/061-2967.20080313.Cnvkg/iPod_25.1.3.ipsw

shasum -a 1 iPod_25.1.3.ipsw
# debe imprimir:
# 0f2dd984c5d56475bdf3c14b4bc7541b799f2c85  iPod_25.1.3.ipsw
```

No ejecutes el parche sobre el IPSW comprimido. La entrada para el parche debe ser la partición de firmware completa obtenida del iPod, de 167.772.160 bytes. Un IPSW de 6,5 MB no contiene el espacio completo de esa partición ni el estado de arranque de cada dispositivo.

## iTunes y el punto de restauración

El delta JSON y la imagen de 160 MiB que genera este paquete **no son un IPSW restaurable por iTunes**. Un IPSW es un archivo ZIP con una imagen de firmware y un `manifest.plist`; iTunes usa ese contenedor para comprobar el modelo, la versión y los componentes antes de restaurar ([formato IPSW](https://theapplewiki.com/wiki/IPSW_File_Format)). Cambiar la imagen exige reconstruir el contenedor y sus metadatos, y una imagen modificada puede ser rechazada por la validación del dispositivo. Este proyecto no genera ni promete un IPSW personalizado.

El flasheador sí crea un punto de restauración local: `firmware-before.bin` conserva la partición original completa y `transaction-target.bin` el candidato generado, junto con hashes, plan y registro de sectores. Es una copia de la partición de firmware, no un respaldo de música ni un respaldo que iTunes pueda importar. Para volver a estado Apple, usa iTunes con el IPSW oficial y después vuelve a aplicar el delta con este paquete si lo deseas. No publiques esos archivos: contienen firmware propietario y metadatos de tu dispositivo.

## Crear una copia propia, sin escribir firmware

Conecta un solo iPod por USB y mantenlo en modo normal. El respaldo desmonta el disco, lee la partición de firmware dos veces y compara ambas lecturas. Guarda la ruta en un disco con al menos 400 MiB libres.

```sh
sudo python3 -I \
  /ruta/al/paquete/backup_shareable_device.py \
  --output /ruta/segura/ipod-share-backup-001
```

El informe debe terminar con `BACKUP VERIFIED; NO DEVICE WRITES`. Conserva los dos archivos; el primero es la entrada para la verificación y el segundo permite demostrar que la lectura se repitió sin cambios.

## Verificar y construir un candidato local

Este paso no toca el iPod. Valida la estructura Apple 1.3, todos los rangos antiguos, la suma OSOS, la conservación de RSRC/AUPD y los bytes no incluidos en el delta.

```sh
python3 -I /ruta/al/paquete/shareable_patch.py \
  verify /ruta/segura/ipod-share-backup-001/firmware-read-1.bin

python3 -I /ruta/al/paquete/shareable_patch.py \
  build /ruta/segura/ipod-share-backup-001/firmware-read-1.bin \
  /ruta/segura/ipod-share-candidate.bin
```

La salida `target_full_sha256` puede diferir del hash de referencia si el volcado conserva bytes de arranque específicos del dispositivo. Eso es esperado: el mismo delta se comprobó también contra una variante de respaldo con SHA-256 `def5d0ae6d992d060a4d03d4a0f5320c747abce82179076f2e3fca2cc81dc746`, y produjo otro hash completo conservando esos metadatos. Deben coincidir `changed_byte_count: 124977`, `physical_sector_count: 155`, la suma OSOS y todos los hashes nativos; los bytes fuera del delta deben quedar iguales.

## Flashear en un iPod compatible

Hazlo solo después de tener el respaldo doble verificado. El iPod debe estar en modo normal, conectado directamente y sin Finder/iTunes sincronizando. El comando crea un directorio `ipod-theme-share-…` en la carpeta actual. Ese directorio contiene el volcado previo, el destino generado, el plan, el registro de sectores y el resultado.

```sh
cd /ruta/segura/al/paquete
sudo python3 -I flash_shareable_patch.py --install
```

El programa ejecuta estas barreras, en este orden:

1. valida el manifiesto local sin abrir ningún dispositivo;
2. exige un único iPod compatible y comprueba geometría y particiones;
3. desmonta el disco y hace dos lecturas completas consecutivas;
4. guarda la primera lectura antes de cualquier escritura;
5. exige los hashes de los rangos originales y recalcula el candidato en memoria;
6. muestra el plan y espera que escribas exactamente `FLASH APPLE 1.3 THEME`;
7. verifica cada sector antes y después de `pwrite`, sincroniza caché y escribe `0x4000` al final;
8. relee los 160 MiB completos y compara contra el candidato generado.

Si las lecturas no coinciden, un rango no coincide, la caché no se puede sincronizar, cambia la identidad USB o falla una escritura, el programa se detiene. Si ya hubo una escritura, no reinicies ni repitas el comando: conserva el directorio de evidencia y revisa el registro.

El resultado correcto es `SHAREABLE THEME WRITTEN AND VERIFIED; BOOT TEST PENDING`. El iPod queda desmontado; no se expulsa ni reinicia automáticamente. Solo después de compartir el resultado puedes desconectar y reiniciar con `Menu + botón central`.

## Restaurar el estado guardado

Usa el mismo iPod y el directorio de evidencia creado por el flasheador. La restauración hace dos lecturas consecutivas de 160 MiB, rechaza cualquier diferencia fuera de los 155 sectores autorizados, pide confirmación y vuelve a verificar la imagen anterior completa.

```sh
sudo python3 -I flash_shareable_patch.py \
  --restore-from /ruta/segura/al/directorio/ipod-theme-share-…
```

El resultado esperado es `ORIGINAL SNAPSHOT RESTORED AND VERIFIED; BOOT TEST PENDING`. Si el medio contiene cambios fuera de los sectores del parche, la restauración automática se niega.

## Qué garantiza y qué no

Los SHA-256, los checksums nativos y la lectura completa detectan discrepancias de bytes. La transacción escribe sectores completos, registra cada intento y conserva un punto de recuperación. Estas comprobaciones reducen el riesgo de una imagen inconsistente; no pueden demostrar que ningún iPod del mundo acepte un firmware modificado ni probar recuperación ante un corte de energía durante la escritura.

La versión compartida reproduce la interfaz integral instalada en un iPod 5.5 de referencia: selector rojo, barras y símbolos coordinados, estados de batería, y jerarquía de texto de «Ahora suena». La nueva navegación de biblioteca, el carrusel de carátulas, el mini-reproductor persistente, streaming, favoritos nuevos y carátulas mayores siguen fuera del alcance. El arranque y el uso deben validarse en cada modelo antes de afirmar compatibilidad.

## Prueba local del paquete

```sh
python3 -I flash_shareable_patch.py --check-local
```

Debe indicar `local_package_valid`, 6.953 rangos, 155 sectores y `device_access: false`. Esta prueba no requiere un iPod y no concede ninguna aprobación para flashear.

Para ejecutar las pruebas offline incluidas, desde esta carpeta:

```sh
python3 -m unittest test_shareable_patch test_flash_shareable -v
```

Las seis pruebas cubren la estructura del manifiesto, reconstrucción de un caso sintético, preservación de metadatos fuera del delta, rechazo de bytes inesperados, orden del checksum y detención ante escritura corta o lectura posterior discrepante. Simulan el medio en memoria; no abren `/dev` ni prueban un corte de energía real.
