"""Read exported AArch64 ELF64 ABI facts without retaining a baseline ELF."""
import struct
from pathlib import Path


def exports(path):
    data = Path(path).read_bytes()
    assert data[:6] == b'\x7fELF\x02\x01', 'expected little-endian ELF64'
    assert struct.unpack_from('<H', data, 18)[0] == 183, 'expected AArch64'
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, names_index = struct.unpack_from('<HHH', data, 58)
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * size) for i in range(count)]

    def section_data(section):
        return data[section[4]:section[4] + section[5]]

    def string(table, position):
        return table[position:table.index(b'\0', position)].decode()

    names = section_data(sections[names_index])
    named = {string(names, section[0]): section for section in sections}
    dynsym = named['.dynsym']
    strings = section_data(sections[dynsym[6]])
    version_data = section_data(named['.gnu.version']) if '.gnu.version' in named else None
    version_names = {}
    if '.gnu.version_d' in named:
        definitions = section_data(named['.gnu.version_d'])
        position = 0
        while position < len(definitions):
            _, _, index, _, _, aux, following = struct.unpack_from('<HHHHIII', definitions, position)
            name, _ = struct.unpack_from('<II', definitions, position + aux)
            version_names[index] = string(strings, name)
            if not following:
                break
            position += following
    result = []
    for index in range(dynsym[5] // dynsym[9]):
        name, info, other, section, _, _ = struct.unpack_from('<IBBHQQ', data, dynsym[4] + index * dynsym[9])
        binding = info >> 4
        if not section or binding not in (1, 2):
            continue
        version = struct.unpack_from('<H', version_data, index * 2)[0] if version_data else 1
        version_index = version & 0x7fff
        assert version_index <= 1 or version_index in version_names, 'unresolved defined-symbol version'
        result.append({'name': string(strings, name), 'binding': 'GLOBAL' if binding == 1 else 'WEAK',
                       'type': info & 15, 'visibility': other & 3,
                       'version': version_names.get(version_index), 'version_index': version_index,
                       'hidden_version': bool(version & 0x8000)})
    return sorted(result, key=lambda symbol: (symbol['name'], symbol['version'] or ''))


def compare(required, actual):
    """Require all baseline name/version facts and compatible export metadata."""
    keys = lambda symbol: (symbol['name'], symbol['version'], symbol['hidden_version'])
    available = {keys(symbol): symbol for symbol in actual}
    assert len(available) == len(actual), 'duplicate rebuilt export key'
    assert len({keys(symbol) for symbol in required}) == len(required), 'duplicate baseline export key'
    missing, mismatches = [], []
    for symbol in required:
        found = available.get(keys(symbol))
        if found is None:
            missing.append(symbol)
        elif found != symbol:
            mismatches.append({'baseline': symbol, 'rebuilt': found})
    return missing, mismatches
