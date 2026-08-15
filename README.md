# El Traker

Pequeña app de escritorio en Python/Tkinter para llevar un registro tipo pomodoro.

## Requisitos
- Python 3.8+ con Tkinter disponible (ya viene con la mayoría de distribuciones).

## Cómo ejecutarlo
```bash
cd /home/emilio/Documentos/El_Traker_Emo
python3 el_traker.py
```

## Qué hace
- Muestra el saludo "Buenos días Emo ¿listo para trabajar? Honra a tu persona amigo." al abrir.
- Permite fijar minutos de pomodoro y de descanso.
- Permite pausar y reanudar el pomodoro actual sin inflar las estadísticas.
- Al iniciar la jornada crea/usa `logs/AAAA-MM-DD.log` y registra:
  - DAY_START / DAY_RESUME
  - POMODORO_START / POMODORO_END
  - BREAK_START / BREAK_END
  - DAY_END
- Alarma (beep + cuadro de diálogo) al terminar pomodoro y descanso.
- Estadísticas por día, semana ISO y mes (minutos totales y número de sesiones).

## Flujo básico
1. Abre la app y coloca los minutos deseados.
2. Presiona "Iniciar jornada". Arranca el primer pomodoro de inmediato.
3. Durante un pomodoro puedes usar "Pausar" y luego "Reanudar".
4. Al terminar cada descanso se habilita "Iniciar pomodoro" para continuar con la siguiente sesión.
5. Presiona "Finalizar jornada" al terminar el día.

## Atajo en el escritorio
Se incluye un lanzador `ElTraker.desktop` dentro del proyecto. Para usarlo:
1. Copia el archivo al escritorio (`~/Desktop` o `~/Escritorio`).
2. Marca el archivo como ejecutable:
   ```bash
   chmod +x ~/Desktop/ElTraker.desktop
   # o
   chmod +x ~/Escritorio/ElTraker.desktop
   ```
3. Ajusta la ruta de `Exec=` en el archivo si moviste la carpeta.

## Archivos
- `el_traker.py`: aplicación principal Tkinter.
- `logs/`: carpeta donde se guardan los registros diarios.
- `ElTraker.desktop`: plantilla de acceso directo.
