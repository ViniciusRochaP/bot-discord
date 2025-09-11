import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import os
# A linha 'from dotenv import load_dotenv' foi removida
from keep_alive import keep_alive

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
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
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
        await interaction.response.edit_message(content="Vaga trocada com sucesso!", view=None)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user != self.user:
            return await interaction.response.send_message("Apenas o jogador original pode cancelar.", ephemeral=True)
        await interaction.response.edit_message(content="Troca cancelada.", view=None)

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
                        await interaction.message.edit(embed=original_embed)
                        await interaction.response.send_message(f"Você se inscreveu como **{clicked_role_name}**!", ephemeral=True)
                        return
                    else:
                        return await interaction.response.send_message("Essa vaga já foi preenchida!", ephemeral=True)

# --- View Principal do Evento ---
class DynamicEventView(View):
    def __init__(self, author_id):
        super().__init__(timeout=None)
        self.author_id = author_id

    async def reorder_buttons(self):
        signup_buttons = sorted([child for child in self.children if isinstance(child, SignupButton)], key=lambda btn: btn.label)
        control_buttons = [child for child in self.children if not isinstance(child, SignupButton)]
        
        self.clear_items()
        for btn in control_buttons:
            self.add_item(btn)
        for btn in signup_buttons:
            self.add_item(btn)

    @discord.ui.button(label="➕ Adicionar Vaga", style=discord.ButtonStyle.success, custom_id="add_role")
    async def add_role_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode adicionar vagas.", ephemeral=True)
        modal = AddRoleModal(original_message=interaction.message, author_id=self.author_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Remover Vaga", style=discord.ButtonStyle.danger, custom_id="remove_role")
    async def remove_role_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode remover vagas.", ephemeral=True)

        embed = interaction.message.embeds[0]
        if not embed.fields:
            return await interaction.response.send_message("Não há vagas para remover.", ephemeral=True)

        options = [discord.SelectOption(label=field.name) for field in embed.fields]
        select = discord.ui.Select(placeholder="Selecione a vaga para remover...", options=options, custom_id="role_remover_select")

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
            await select_interaction.response.edit_message(content=f"Vaga '{role_to_remove}' removida.", view=None)

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
        self.add_item(TextInput(label="Nome da Vaga", placeholder="Ex: Tank, Healer, DPS Range...", required=True))

    async def on_submit(self, interaction: discord.Interaction):
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
        await interaction.response.send_message(f"Vaga '{role_name}' adicionada!", ephemeral=True)

# --- Comando Principal ---
@bot.tree.command(name="criar_evento", description="Cria um novo evento para PTs de Albion.")
async def criar_evento(
    interaction: discord.Interaction, 
    titulo: str, 
    horario: str, 
    descricao: str = "Sem descrição."
):
    embed = discord.Embed(
        title=f"📢 Evento: {titulo}",
        description=f"**Horário:** {horario}\n**Descrição:** {descricao}\n\n**Vagas:**",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Evento criado por {interaction.user.display_name}")
    embed.set_thumbnail(url="https://assets.albiononline.com/assets/images/items/T8_CHEST_AVALONIAN_ELITE.png")

    view = DynamicEventView(author_id=interaction.user.id)
    await interaction.response.send_message(f"@everyone, novo evento '{titulo}' criado!", embed=embed, view=view)

# --- Evento de Inicialização ---
@bot.event
async def on_ready():
    print(f'Bot {bot.user} está online e pronto!')
    try:
        synced = await bot.tree.sync()
        print(f"Sincronizado {len(synced)} comando(s).")
    except Exception as e:
        print(f"Erro ao sincronizar comandos: {e}")
    
# --- Ligar o Bot ---
if __name__ == "__main__":
    keep_alive()
    # Puxa o token diretamente do ambiente do servidor (Render)
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("ERRO CRÍTICO: Token do Discord não foi encontrado. Verifique as variáveis de ambiente no Render.")
