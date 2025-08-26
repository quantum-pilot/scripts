import os
import shutil
import sys
import time
from multiprocessing import Pool
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


MAX_WIDTH = 20000
QUALITY = 90
TARGET_DIR = sys.argv[1] if len(sys.argv) > 1 else '.'

REPLACE_EXT = ('.bmp', 'gif', '.png', '.jpg', '.jpeg')


def convert_to_sibling(f):
    global QUALITY, MAX_WIDTH
    old = None
    _, ext = os.path.splitext(f)
    if ext.lower() in REPLACE_EXT:
        old = f
        f = f.replace(ext, ".webp")
    else:
        print(f'skipping: {old}')
        return
    with Image.open(old) as img:
        if img.mode in ('P', 'RGBA'):
            img = img.convert('RGB')
        w, h = img.size
        cw = MAX_WIDTH
        if w > h:
            cw = MAX_WIDTH * 2
        if cw < w:
            print(f'Scaling {f}')
            img.thumbnail((cw, cw), Image.Resampling.LANCZOS)
        try:
            img.save(f, quality=QUALITY, method=6)
            return
        except ValueError:
            pass
    print(f"Copying {old} to {f}")
    shutil.copyfile(old, f)


def sizeof_fmt(num, suffix="B"):
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def get_size(start_path = '.'):
    total = 0
    total_size = 0
    for dirpath, _dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total += 1
                total_size += os.path.getsize(fp)
    return total, total_size


def flatten_single_dirs(d):
    l = os.listdir(d)
    if len(l) == 0:
        print(f"Empty directory: {d}")
        return
    i = os.path.join(d, l[0])
    if os.path.isdir(i) and len(l) == 1:
        print(f'Flattening {i} into {d}')
        for f in os.listdir(i):
            os.rename(os.path.join(i, f), os.path.join(d, f))
        os.rmdir(i)


if __name__ == '__main__':
    total_size = 0
    total_reduced = 0
    p = Pool()
    ordering = []
    print("CWD:", TARGET_DIR)
    for d in os.listdir(TARGET_DIR):
        d = os.path.join(TARGET_DIR, d)
        if os.path.isdir(d):
            print("Checking", d)
            flatten_single_dirs(d)
            n, s = get_size(d)
            if n == 0:
                continue
            ratio = round((s / 1024**2) / n, 2)
            files = os.listdir(d)
            if ratio >= 1 or any('.' + f.lower().split('.')[-1] in REPLACE_EXT for f in files):
                ordering.append((d, n, sizeof_fmt(s), ratio))
    for d, n, s, ratio in sorted(ordering, key = lambda v: v[3]):
        print(f'{d}: {s} / {n} = {ratio}')
        files = []
        expected_files = []
        for f in os.listdir(d):
            f = os.path.join(d, f)
            _, ext = os.path.splitext(f)
            if ext.lower() in REPLACE_EXT:
                files.append(f)
                expected_files.append(f.replace(ext, '.webp'))
        initial_size = sum(os.stat(f).st_size for f in files)
        total_size += initial_size
        print(f'({d}): {len(files)} files (size: {sizeof_fmt(initial_size)}), {MAX_WIDTH=}, {QUALITY=}')
        p.map(convert_to_sibling, files)
        final_size = sum(os.stat(f).st_size for f in expected_files)
        print(f'Final size: {sizeof_fmt(final_size)}')
        files_to_remove = expected_files
        if final_size < initial_size:
            files_to_remove = files
            total_reduced += (initial_size - final_size)
        if files_to_remove:
            print(f'Removing {files_to_remove[0]} in {d}')
            for j in files_to_remove:
                while True:
                    try:
                        os.remove(j)
                        break
                    except PermissionError as err:
                        print(err)
                        time.sleep(1)
                        print(f'Attempting to remove {j}')
        print(f'Total work: {sizeof_fmt(total_size)}, reduced: {sizeof_fmt(total_reduced)}')
