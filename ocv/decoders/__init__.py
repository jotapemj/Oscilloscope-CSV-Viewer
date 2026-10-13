"""Protocol decoders and the registry the UI reads."""
from ocv.decoders.arinc429 import Arinc429Decoder
from ocv.decoders.can import CanDecoder, CanFdDecoder
from ocv.decoders.i2c import I2CDecoder
from ocv.decoders.i2s import I2SDecoder
from ocv.decoders.j1939 import J1939Decoder
from ocv.decoders.lin import LinDecoder
from ocv.decoders.mil1553 import MilStd1553Decoder
from ocv.decoders.sdio import SdioDecoder
from ocv.decoders.spi import SPIDecoder
from ocv.decoders.uart import UartDecoder


# Registro de decoders disponibles. Para añadir uno nuevo: subclase de
# ProtocolDecoder + entrada aquí, y añadirlo a la categoría correspondiente
# en DECODER_CATEGORIES. La UI lo recoge automáticamente.
DECODER_REGISTRY = [
    UartDecoder, I2CDecoder, SPIDecoder,
    LinDecoder, I2SDecoder, SdioDecoder,
    CanDecoder, CanFdDecoder, J1939Decoder,
    MilStd1553Decoder, Arinc429Decoder,
]

# Categorías para el acordeón estilo MEAS. El orden de los grupos y el
# orden dentro de cada grupo se respeta en la UI.
DECODER_CATEGORIES = {
    "SERIAL / INDUSTRIAL": [UartDecoder],
    "EMBEDDED": [I2CDecoder, SPIDecoder, I2SDecoder, SdioDecoder],
    "AUTOMOTIVE": [LinDecoder, CanDecoder, CanFdDecoder, J1939Decoder],
    "AVIONICS / MILITARY": [MilStd1553Decoder, Arinc429Decoder],
}
