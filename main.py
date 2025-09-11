import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import os
import json
from keep_alive import keep_alive

# --- Configuração Inicial do Bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Carregar/Salvar Templates ---
def load_templates():
    if os.path.exists("templates.json"):
        with open("templates.json", "r") as f:
            return json.load(f)
    return {}

def save_templates(templates):
    with open("templates.json", "w") as f:
        json.dump(templates, f, indent=4)

templates = load_templates()

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
            for child in self.children:
                if isinstance(child, SignupButton) and child.label != role_to_remove:
                    new_view.add_item(SignupButton(label=child.label))

            for control_button in [c for c in self.children if not isinstance(c, SignupButton)]:
                new_view.add_item(control_button)

            await new_view.reorder_buttons()
            
            await interaction.message.edit(embed=new_embed, view=new_view)
            await select_interaction.response.edit_message(content=f"Vaga '{role_to_remove}' removida.", view=None)

        select.callback = select_callback
        view = View()
        view.add_item(select)
        await interaction.response.send_message("Qual vaga você deseja remover?", view=view, ephemeral=True)

    @discord.ui.button(label="✅ Concluir Evento", style=discord.ButtonStyle.primary, custom_id="conclude_event")
    async def conclude_event_button(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode concluir o evento.", ephemeral=True)
        
        view = ConcludeView(self.author_id, interaction.message)
        await interaction.response.send_message("O evento foi cancelado?", view=view, ephemeral=True)

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

        # Re-creating the view to add the new button
        current_view = View.from_message(self.original_message)
        new_view = DynamicEventView(author_id=self.author_id)
        
        # Add existing buttons
        for child in current_view.children:
            if not isinstance(child, SignupButton):
                new_view.add_item(child)

        for field in embed.fields:
            new_view.add_item(SignupButton(label=field.name))

        await new_view.reorder_buttons()

        await self.original_message.edit(embed=embed, view=new_view)
        await interaction.response.send_message(f"Vaga '{role_name}' adicionada!", ephemeral=True)

# --- Views e Modals para Concluir Evento ---
class ConcludeView(View):
    def __init__(self, author_id, original_message):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.original_message = original_message

    @discord.ui.button(label="Sim", style=discord.ButtonStyle.danger)
    async def yes_button(self, interaction: discord.Interaction, button: Button):
        await self.original_message.edit(content="Evento cancelado.", view=None)
        await interaction.response.edit_message(content="Evento marcado como cancelado.", view=None)

    @discord.ui.button(label="Não", style=discord.ButtonStyle.success)
    async def no_button(self, interaction: discord.Interaction, button: Button):
        modal = LootRepairModal(self.author_id, self.original_message)
        await interaction.response.send_modal(modal)

class LootRepairModal(Modal):
    def __init__(self, author_id, original_message):
        super().__init__(title="Detalhes do Evento")
        self.author_id = author_id
        self.original_message = original_message
        self.add_item(TextInput(label="Loot Total", placeholder="Apenas números", required=True))
        self.add_item(TextInput(label="Reparo Total", placeholder="Apenas números", required=True))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            total_loot = int(self.children[0].value)
            total_repair = int(self.children[1].value)
        except ValueError:
            return await interaction.response.send_message("Por favor, insira apenas números para o loot e reparo.", ephemeral=True)

        embed = self.original_message.embeds[0]
        participants = [field for field in embed.fields if "Vazio" not in field.value]
        
        if not participants:
            return await interaction.response.send_message("Não há participantes no evento para dividir o loot.", ephemeral=True)

        net_loot = total_loot - total_repair
        payout_per_person = net_loot // len(participants)

        report_channel = bot.get_channel(1415693614989836358)
        
        report_embed = discord.Embed(
            title=f"Relatório do Evento: {embed.title}",
            description=f"**Loot Total:** {total_loot:,}\n**Reparo Total:** {total_repair:,}\n**Loot Líquido:** {net_loot:,}\n**Pagamento por Pessoa:** {payout_per_person:,}",
            color=discord.Color.green()
        )
        
        participants_mentions = []
        for field in participants:
            # Assuming the value is a user mention
            participants_mentions.append(field.value)

        view = PaymentView(self.author_id, participants_mentions, report_embed)

        await report_channel.send(embed=report_embed, view=view)
        await interaction.response.send_message("Relatório do evento enviado!", ephemeral=True)
        await self.original_message.edit(content="Evento concluído.", view=None)


class PaymentView(View):
    def __init__(self, author_id, participants, embed):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.participants = participants
        self.embed = embed
        self.paid_status = {p: False for p in participants}

        for participant in self.participants:
            self.add_item(PaymentButton(participant, self.paid_status))
        self.update_embed()

    def update_embed(self):
        self.embed.clear_fields()
        for participant in self.participants:
            status = "Pago" if self.paid_status[participant] else "Não Pago"
            self.embed.add_field(name=participant, value=status, inline=False)

class PaymentButton(Button):
    def __init__(self, participant, paid_status):
        super().__init__(label=f"Confirmar Pagamento {participant}", style=discord.ButtonStyle.secondary, custom_id=f"pay_{participant}")
        self.participant = participant
        self.paid_status = paid_status

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.view.author_id:
            return await interaction.response.send_message("Apenas o criador do evento pode confirmar o pagamento.", ephemeral=True)

        self.paid_status[self.participant] = not self.paid_status[self.participant] # Toggle status
        self.style = discord.ButtonStyle.success if self.paid_status[self.participant] else discord.ButtonStyle.secondary
        self.view.update_embed()
        await interaction.message.edit(embed=self.view.embed, view=self.view)
        await interaction.response.defer()

# --- Comandos ---
@bot.tree.command(name="criar_evento", description="Cria um novo evento para PTs de Albion.")
async def criar_evento(
    interaction: discord.Interaction, 
    titulo: str, 
    horario: str, 
    descricao: str = "Sem descrição.",
    template: str = None
):
    embed = discord.Embed(
        title=f"📢 Evento: {titulo}",
        description=f"**Horário:** {horario}\n**Descrição:** {descricao}\n\n**Vagas:**",
        color=discord.Color.gold()
    )
    embed.set_footer(text=f"Evento criado por {interaction.user.display_name}")
    embed.set_thumbnail(url="https://assets.albiononline.com/assets/images/items/T8_CHEST_AVALONIAN_ELITE.png")

    view = DynamicEventView(author_id=interaction.user.id)

    if template and template in templates:
        for role in templates[template]:
            embed.add_field(name=role, value="Vazio", inline=False)
            view.add_item(SignupButton(label=role))

    await view.reorder_buttons()
    await interaction.response.send_message(f"@everyone, novo evento '{titulo}' criado!", embed=embed, view=view)

@bot.tree.command(name="criar_template", description="Cria um novo template de vagas.")
async def criar_template(interaction: discord.Interaction, nome: str, vagas: str):
    vagas_list = [v.strip() for v in vagas.split(',')]
    templates[nome] = vagas_list
    save_templates(templates)
    await interaction.response.send_message(f"Template '{nome}' criado com as vagas: {', '.join(vagas_list)}", ephemeral=True)

@bot.tree.command(name="listar_templates", description="Lista todos os templates salvos.")
async def listar_templates(interaction: discord.Interaction):
    if not templates:
        return await interaction.response.send_message("Nenhum template salvo.", ephemeral=True)

    embed = discord.Embed(title="Templates Salvos", color=discord.Color.blue())
    for name, roles in templates.items():
        embed.add_field(name=name, value=", ".join(roles), inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="excluir_template", description="Exclui um template salvo.")
async def excluir_template(interaction: discord.Interaction, nome: str):
    if nome in templates:
        del templates[nome]
        save_templates(templates)
        await interaction.response.send_message(f"Template '{nome}' excluído com sucesso.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Template '{nome}' não encontrado.", ephemeral=True)

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
