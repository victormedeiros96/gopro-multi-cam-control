import sys
import os
import glob

EXTENSOES_LIXO = {".lrv", ".thm"}


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."

    if os.path.isfile(target):
        base = os.path.dirname(target)
    else:
        base = target

    arquivos = []
    for ext in EXTENSOES_LIXO:
        arquivos.extend(glob.glob(os.path.join(base, "**", f"*{ext}"), recursive=True))

    if not arquivos:
        print("Nenhum arquivo .lrv / .thm encontrado.")
        return

    print(f"Encontrados {len(arquivos)} arquivos .lrv/.thm para apagar:")
    for f in arquivos[:10]:
        print(f"  {os.path.relpath(f, base)}")
    if len(arquivos) > 10:
        print(f"  ... e mais {len(arquivos) - 10}")

    resp = input("Apagar? [s/N]: ").strip().lower()
    if resp != "s":
        print("Cancelado.")
        return

    apagados = 0
    for f in arquivos:
        try:
            os.remove(f)
            apagados += 1
        except OSError as e:
            print(f"  Erro ao apagar {f}: {e}")

    print(f"{apagados} arquivo(s) apagado(s).")


if __name__ == "__main__":
    main()
