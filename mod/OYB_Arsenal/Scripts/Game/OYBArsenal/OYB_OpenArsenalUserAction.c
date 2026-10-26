// "Open OYB Arsenal", added next to the arsenal's own actions. It only opens a menu
// on the player's own screen; the vanilla arsenal actions are left as they are.
class OYB_OpenArsenalUserAction : ScriptedUserAction
{
	override bool CanBeShownScript(IEntity user)
	{
		return SCR_ArsenalComponent.FindArsenalComponent(GetOwner()) != null;
	}

	override bool CanBePerformedScript(IEntity user)
	{
		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(GetOwner());
		if (!arsenal || !arsenal.IsArsenalEnabled())
		{
			SetCannotPerformReason("Arsenal disabled");
			return false;
		}
		return true;
	}

	override void PerformAction(IEntity pOwnerEntity, IEntity pUserEntity)
	{
		if (pUserEntity != SCR_PlayerController.GetLocalControlledEntity())
			return;

		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(pOwnerEntity);
		if (!arsenal)
			return;

		OYB_ArsenalMenu.SetPendingArsenal(arsenal);
		GetGame().GetMenuManager().OpenMenu(ChimeraMenuPreset.OYB_ArsenalMenu, 0, true);
	}

	override bool HasLocalEffectOnlyScript()
	{
		return true;
	}
}
