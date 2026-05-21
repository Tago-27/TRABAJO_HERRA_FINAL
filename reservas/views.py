from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.mail import send_mail
from django.contrib.auth.models import User
from django.utils.crypto import get_random_string
from django.contrib.auth.hashers import make_password

from .forms import RegistroForm, LoginForm, CanchaForm, ReservaForm
from .models import Cancha, Reserva
from .pdf_utils import generar_pdf_reservas


# ── PUBLICAS ──

def inicio(request):
    canchas = Cancha.objects.filter(disponible=True)
    return render(request, 'reservas/inicio.html', {'canchas': canchas})


def registro(request):
    if request.user.is_authenticated:
        return redirect('inicio')
    if request.method == 'POST':
        form = RegistroForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Bienvenido, {user.first_name}! Tu cuenta fue creada.')
            return redirect('inicio')
        messages.error(request, 'Por favor corrige los errores.')
    else:
        form = RegistroForm()
    return render(request, 'reservas/registro.html', {'form': form})


def iniciar_sesion(request):
    if request.user.is_authenticated:
        return redirect('inicio')
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'Hola de nuevo, {user.first_name or user.username}!')
            return redirect('admin_panel' if user.is_staff else 'inicio')
        messages.error(request, 'Usuario o contrasena incorrectos.')
    else:
        form = LoginForm()
    return render(request, 'reservas/login.html', {'form': form})


def cerrar_sesion(request):
    logout(request)
    messages.info(request, 'Has cerrado sesion correctamente.')
    return redirect('inicio')


# ── RECUPERACION DE CONTRASENA ──

def recuperar_contrasena(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email=email)
            nueva = get_random_string(10)
            user.password = make_password(nueva)
            user.save()
            send_mail(
                subject='CanchasSport — Tu nueva contraseña',
                message=(
                    f'Hola {user.first_name or user.username},\n\n'
                    f'Tu nueva contraseña temporal es: {nueva}\n\n'
                    'Por seguridad, cámbiala después de iniciar sesión.\n\n'
                    '— Equipo CanchasSport'
                ),
                from_email=None,  # usa DEFAULT_FROM_EMAIL de settings
                recipient_list=[email],
                fail_silently=False,
            )
            messages.success(request, 'Te enviamos una contraseña temporal a tu correo.')
        except User.DoesNotExist:
            # No revelar si el correo existe o no
            messages.success(request, 'Si ese correo está registrado, recibirás instrucciones.')
        return redirect('login')
    return render(request, 'reservas/recuperar_contrasena.html')


# ── CLIENTE ──

@login_required
def hacer_reserva(request, cancha_id):
    cancha = get_object_or_404(Cancha, id=cancha_id, disponible=True)
    if request.method == 'POST':
        form = ReservaForm(request.POST, cancha=cancha)
        if form.is_valid():
            reserva = form.save(commit=False)
            reserva.usuario = request.user
            reserva.cancha = cancha
            reserva.estado = 'pendiente'
            reserva.save()
            messages.success(request, f'Reserva realizada para el {reserva.fecha} de {reserva.hora_inicio.strftime("%H:%M")} a {reserva.hora_fin.strftime("%H:%M")}.')
            return redirect('mis_reservas')
        messages.error(request, 'Revisa los datos de la reserva.')
    else:
        form = ReservaForm(cancha=cancha)
    return render(request, 'reservas/hacer_reserva.html', {'form': form, 'cancha': cancha})


@login_required
def horarios_ocupados(request, cancha_id):
    """Devuelve JSON con los horarios ocupados de una cancha para una fecha dada."""
    cancha = get_object_or_404(Cancha, id=cancha_id, disponible=True)
    fecha = request.GET.get('fecha')
    if not fecha:
        return JsonResponse({'horarios': []})
    reservas = Reserva.objects.filter(
        cancha=cancha,
        fecha=fecha,
        estado__in=['pendiente', 'confirmada']
    ).values('hora_inicio', 'hora_fin')
    horarios = [
        {'inicio': r['hora_inicio'].strftime('%H:%M'), 'fin': r['hora_fin'].strftime('%H:%M')}
        for r in reservas
    ]
    return JsonResponse({'horarios': horarios})


@login_required
def mis_reservas(request):
    reservas = Reserva.objects.filter(usuario=request.user).order_by('-fecha_creacion')
    return render(request, 'reservas/mis_reservas.html', {'reservas': reservas})


@login_required
def cancelar_reserva(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id, usuario=request.user)
    if reserva.estado in ['pendiente', 'confirmada']:
        reserva.estado = 'cancelada'
        reserva.save()
        messages.success(request, 'Reserva cancelada correctamente.')
    else:
        messages.error(request, 'Esta reserva no puede ser cancelada.')
    return redirect('mis_reservas')


# ── ADMIN ──

@staff_member_required
def admin_panel(request):
    # Filtros
    tipo_filtro = request.GET.get('tipo', '')
    estado_filtro = request.GET.get('estado_cancha', '')

    canchas = Cancha.objects.all()
    if tipo_filtro:
        canchas = canchas.filter(tipo=tipo_filtro)
    if estado_filtro == 'activa':
        canchas = canchas.filter(disponible=True)
    elif estado_filtro == 'inactiva':
        canchas = canchas.filter(disponible=False)

    reservas = Reserva.objects.all().order_by('-fecha_creacion')[:20]
    return render(request, 'reservas/admin_panel.html', {
        'canchas': canchas,
        'reservas': reservas,
        'total_canchas': Cancha.objects.count(),
        'total_reservas': Reserva.objects.count(),
        'reservas_pendientes': Reserva.objects.filter(estado='pendiente').count(),
        'reservas_confirmadas': Reserva.objects.filter(estado='confirmada').count(),
        'tipo_filtro': tipo_filtro,
        'estado_filtro': estado_filtro,
        'tipos_cancha': Cancha.TIPOS,
    })


@staff_member_required
def agregar_cancha(request):
    if request.method == 'POST':
        form = CanchaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cancha agregada correctamente.')
            return redirect('admin_panel')
        messages.error(request, 'Revisa los datos del formulario.')
    else:
        form = CanchaForm()
    return render(request, 'reservas/cancha_form.html', {'form': form, 'accion': 'Agregar'})


@staff_member_required
def editar_cancha(request, cancha_id):
    cancha = get_object_or_404(Cancha, id=cancha_id)
    if request.method == 'POST':
        form = CanchaForm(request.POST, instance=cancha)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cancha actualizada correctamente.')
            return redirect('admin_panel')
        messages.error(request, 'Revisa los datos del formulario.')
    else:
        form = CanchaForm(instance=cancha)
    return render(request, 'reservas/cancha_form.html', {'form': form, 'accion': 'Editar', 'cancha': cancha})


@staff_member_required
def eliminar_cancha(request, cancha_id):
    cancha = get_object_or_404(Cancha, id=cancha_id)
    if request.method == 'POST':
        cancha.delete()
        messages.success(request, 'Cancha eliminada.')
        return redirect('admin_panel')
    return render(request, 'reservas/confirmar_eliminar.html', {'cancha': cancha})


@staff_member_required
def deshabilitar_cancha(request, cancha_id):
    """Alterna el estado disponible de una cancha."""
    cancha = get_object_or_404(Cancha, id=cancha_id)
    if request.method == 'POST':
        cancha.disponible = not cancha.disponible
        cancha.save()
        estado = 'habilitada' if cancha.disponible else 'deshabilitada'
        messages.success(request, f'Cancha {estado} correctamente.')
    return redirect('admin_panel')


@staff_member_required
def cambiar_estado_reserva(request, reserva_id):
    reserva = get_object_or_404(Reserva, id=reserva_id)
    nuevo_estado = request.POST.get('estado')
    if nuevo_estado in ['pendiente', 'confirmada', 'cancelada']:
        reserva.estado = nuevo_estado
        reserva.save()
        messages.success(request, f'Estado actualizado a {reserva.get_estado_display()}.')
    return redirect('admin_panel')


@staff_member_required
def descargar_pdf_reservas(request):
    filtro = request.GET.get('estado', 'todas')
    reservas = Reserva.objects.all().order_by('-fecha_creacion').select_related('usuario', 'cancha')
    if filtro in ['pendiente', 'confirmada', 'cancelada']:
        reservas = reservas.filter(estado=filtro)
    titulo_map = {
        'todas': 'Reporte Completo de Reservas',
        'pendiente': 'Reservas Pendientes',
        'confirmada': 'Reservas Confirmadas',
        'cancelada': 'Reservas Canceladas',
    }
    return generar_pdf_reservas(reservas, titulo_map.get(filtro, 'Reporte de Reservas'))
