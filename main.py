import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput # <--- CORRIGIDO AQUI
import os
from keep_alive import keep_alive # Certifique-se que você tem o arquivo keep_alive.py

# --- Configuração Inicial do Bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Lógica de Confirmação de Troca ---
class ConfirmationView(View):
    def __init__(self, user, old_role_name, new_role_name, original_embed, original_message):
        super().__init__(timeout=60)
        self.user = user
        self.old_role_name = old_role_name
        self.new_role_name = new_role_name
        self.original_embed = original_embed
        self.original_message = original_message

    @discord.ui.button(label="Sim, quero trocar!", style=discord.ButtonStyle.success)
    async def confirm_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user != self.user:
            return await interaction.response.send_message("Apenas o jogador original pode confirmar a troca.", ephemeral=True)

        old_role_index = -1
        new_role_index = -1
        for i, field in enumerate(self.original_embed.fields):
            if field.name == self.old_role_name: old_role_index = i
            if field.name == self.new_role_name: new_role_index = i

        self.original_embed.set_field_at(old_role_index, name=self.old_role_name, value="Vazio", inline=False)
        self.original_embed.set_field_at(new_role_index, name=self.new_role_name, value=self.user.mention, inline=False)

        await self.original_message.edit(embed=self.original_embed)
        await interaction.response.defer()
        await interaction.delete_original_response()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger)
    async def cancel_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user != self.user:
            return await interaction.response.send_message("Apenas o jogador original pode cancelar.", ephemeral=True)
        await interaction.response.defer()
        await interaction.delete_original_response()


# --- Botão de Inscrição ---
class SignupButton(Button):
    def __init__(self, label):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=f"signup_{label}")

    async def callback(self, interaction: discord.Interaction):
        original_embed = interaction.message.embeds[0]
        user = interaction.user
        clicked_role_name = self.label

        def get_user_current_role_field(user_mention, embed):
            return next((field for field in embed.fields if user_mention in field.value), None)

        current_role_field = get_user_current_role_field(user.mention, original_embed)

        if current_role_field:
            if current_role_field.name == clicked_role_name:
                return await interaction.response.send_message("Você já está inscrito nesta vaga.", ephemeral=True)
            else:
                new_role_field = next((f for f in original_embed.fields if f.name == clicked_role_name), None)
                if "Vazio" not in new_role_field.value:
                    return await interaction.response.send_message(f"A vaga de **{clicked_role_name}** já foi preenchida.", ephemeral=True)

                view = ConfirmationView(user, current_role_field.name, clicked_role_name, original_embed, interaction.message)
                await interaction.response.send_message(f"Deseja trocar da vaga **{current_role_field.name}** para **{clicked_role_name}**?", view=view, ephemeral=True)
        else:
            for i, field in enumerate(original_embed.fields):
                if field.name == clicked_role_name:
                    if "Vazio" in field.value:
                        original_embed.set_field_at(i, name=field.name, value=user.mention, inline=False)
                        await interaction.response.edit_message(embed=original_embed)
                        return
                    else:
                        return await interaction.response.send_message("Essa vaga já foi preenchida!", ephemeral=True)

# --- View Principal do Evento ---
class DynamicEventView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id

    async def reorder_buttons(self):
        signup_buttons = [child for child in self.children if isinstance(child, SignupButton)]
        control_buttons = [child for child in self.children if not isinstance(child, SignupButton)]

        self.clear_items()
        for btn in control_buttons:
            self.add_item(btn)
        for btn in signup_buttons:
            self.add_item(btn)

    @discord.ui.button(label="➕ Adicionar Vaga", style=discord.ButtonStyle.success, custom_id="add_role")
    async def add_role_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode adicionar vagas.", ephemeral=True)

        modal = AddRoleModal(original_message=interaction.message, author_id=self.author_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Remover Vaga", style=discord.ButtonStyle.danger, custom_id="remove_role")
    async def remove_role_button(self, button: Button, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode remover vagas.", ephemeral=True)

        embed = interaction.message.embeds[0]
        if not embed.fields:
            return await interaction.response.send_message("Não há vagas para remover.", ephemeral=True)

        options = [discord.SelectOption(label=field.name) for field in embed.fields]
        select = discord.ui.Select(placeholder="Selecione a vaga para remover...", options=options)

        async def select_callback(select_interaction: discord.Interaction):
            role_to_remove = select_interaction.data['values'][0]

            new_embed = interaction.message.embeds[0]
            new_fields = [field for field in new_embed.fields if field.name != role_to_remove]
            new_embed.clear_fields()
            for field in new_fields:
                new_embed.add_field(name=field.name, value=field.value, inline=False)

            new_view = DynamicEventView(author_id=self.author_id)
            for field in new_embed.fields:
                new_view.add_item(SignupButton(label=field.name))
            await new_view.reorder_buttons()

            await interaction.message.edit(embed=new_embed, view=new_view)
            await select_interaction.response.defer()

        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Qual vaga você deseja remover?", view=view, ephemeral=True)

# --- Modal para Adicionar Vaga ---
class AddRoleModal(Modal):
    def __init__(self, original_message, author_id):
        super().__init__(title="Adicionar Nova Vaga")
        self.original_message = original_message
        self.author_id = author_id
        # CORRIGIDO AQUI
        self.add_item(TextInput(label="Nome da Vaga", placeholder="Ex: Tank, Healer, DPS Range...", required=True))

    async def callback(self, interaction: discord.Interaction):
        role_name = self.children[0].value.strip()
        embed = self.original_message.embeds[0]

        if any(field.name.lower() == role_name.lower() for field in embed.fields):
            return await interaction.response.send_message(f"A vaga '{role_name}' já existe.", ephemeral=True)

        embed.add_field(name=role_name, value="Vazio", inline=False)

        new_view = DynamicEventView(author_id=self.author_id)
        for field in embed.fields:
            new_view.add_item(SignupButton(label=field.name))
        await new_view.reorder_buttons()

        await self.original_message.edit(embed=embed, view=new_view)
        await interaction.response.defer()

# --- Comando Principal ---
@bot.slash_command(name="criar_evento", description="Cria um novo evento para PTs de Albion.")
async def criar_evento(
    ctx: discord.ApplicationContext,
    titulo: discord.Option(str, "Qual o título do evento?", required=True),
    horario: discord.Option(str, "Qual o dia e hora do evento? (ex: 21:00 de Sábado)", required=True),
    descricao: discord.Option(str, "Uma breve descrição do evento.", required=False, default="Sem descrição.")
):
    embed = discord.Embed(
        title=f"📢 Evento: {titulo}",
        description=f"**Horário:** {horario}\n**Descrição:** {descricao}\n\n**Vagas:**",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Evento criado por {ctx.author.display_name}")
    embed.set_thumbnail(url="https://assets.albiononline.com/assets/images/items/T8_CHEST_AVALONIAN_ELITE.png")

    view = DynamicEventView(author_id=ctx.author.id)
    await ctx.respond(f"@everyone, novo evento '{titulo}' criado!", embed=embed, view=view)

# --- Evento de Inicialização ---
@bot.event
async def on_ready():
    bot.add_view(DynamicEventView(author_id=0))
    print(f'Bot {bot.user} está online e pronto!')

# --- Ligar o Bot ---
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
