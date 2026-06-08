import struct
import zipfile

def parse_axml_package_name(data):
    magic, size = struct.unpack('<II', data[0:8])
    if magic != 0x00080003:
        raise ValueError("Not a valid binary AndroidManifest.xml")
        
    offset = 8
    chunk_type, chunk_size = struct.unpack('<II', data[offset:offset+8])
    if chunk_type != 0x001C0001:
        raise ValueError("String pool chunk not found")
        
    string_count, style_count, flags, string_offset, style_offset = struct.unpack(
        '<IIIII', data[offset+8:offset+28]
    )
    
    is_utf8 = (flags & 256) != 0
    
    offsets_start = offset + 28
    offsets = []
    for i in range(string_count):
        off = struct.unpack('<I', data[offsets_start + i*4:offsets_start + i*4 + 4])[0]
        offsets.append(off)
        
    strings_start = offset + string_offset
    strings = []
    for i in range(string_count):
        start = strings_start + offsets[i]
        if is_utf8:
            # Decode UTF-8 string:
            # UTF-8 strings start with 1 or 2 bytes length, then 1 or 2 bytes byte length
            val = data[start]
            if val & 0x80:
                start += 2
            else:
                start += 1
            length = data[start]
            if length & 0x80:
                start += 2
            else:
                start += 1
            string_data = data[start:start+length].decode('utf-8', errors='ignore')
            strings.append(string_data)
        else:
            # Decode UTF-16 string:
            length = struct.unpack('<H', data[start:start+2])[0]
            if length & 0x8000:
                start += 4
            else:
                start += 2
            string_data = data[start:start+length*2].decode('utf-16le', errors='ignore')
            strings.append(string_data)

    offset += chunk_size
    if offset < len(data):
        chunk_type, chunk_size = struct.unpack('<II', data[offset:offset+8])
        if chunk_type == 0x00080180:
            offset += chunk_size
            
    while offset < len(data):
        if offset + 8 > len(data):
            break
        c_type, c_size = struct.unpack('<II', data[offset:offset+8])
        if c_type == 0x00100102: # Start Tag
            # Read name_idx at offset + 20
            name_idx = struct.unpack('<i', data[offset+20:offset+24])[0]
            tag_name = strings[name_idx] if 0 <= name_idx < len(strings) else ""
            if tag_name == "manifest":
                # Attributes start at offset + 36 (AXML standard start tag header is 36 bytes)
                attr_count = struct.unpack('<H', data[offset+28:offset+30])[0]
                for a in range(attr_count):
                    a_start = offset + 36 + a * 20
                    if a_start + 12 > len(data):
                        break
                    a_ns, a_name, a_raw_val = struct.unpack('<iii', data[a_start:a_start+12])
                    attr_name = strings[a_name] if 0 <= a_name < len(strings) else ""
                    if attr_name == "package":
                        return strings[a_raw_val] if 0 <= a_raw_val < len(strings) else None
        if c_size <= 0:
            break
        offset += c_size
    return None

try:
    apk_path = "/home/jordanh/Documents/timekpr-webui/android-agent/app/build/outputs/apk/debug/app-debug.apk"
    with zipfile.ZipFile(apk_path) as z:
        manifest_data = z.read("AndroidManifest.xml")
        package_name = parse_axml_package_name(manifest_data)
        print("SUCCESS! Package Name:", package_name)
except Exception as e:
    import traceback
    print("FAILED:", e)
    traceback.print_exc()
