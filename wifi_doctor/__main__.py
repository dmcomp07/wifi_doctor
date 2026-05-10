import tkinter as tk
from wifi_doctor.gui.app import WifiDoctorApp
from wifi_doctor.utils.shell import is_windows

def main():
    if not is_windows():
        print("WiFi Doctor requires Windows.")
        return
    root = tk.Tk()
    app = WifiDoctorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
