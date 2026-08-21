from django.db import models

class ChatSession(models.Model):
    title = models.CharField(max_length=255, default="Sesión de Depuración")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Sesión {self.id} - {self.title}"


class Message(models.Model):
    ROLE_CHOICES = [
        ('user', 'Usuario'),
        ('assistant', 'PatitoJar'),
        ('system', 'Sistema'),
    ]

    session = models.ForeignKey(ChatSession, related_name='messages', on_delete=models.CASCADE)
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default='user')
    content = models.TextField()
    code_context = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"[{self.role}] {self.timestamp.strftime('%H:%M:%S') if self.timestamp else ''}"
