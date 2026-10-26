// Double-clicking an attachment in the arsenal (or dragging it onto the character)
// fits it straight onto the weapon in the player's hands. If that slot already has
// something in it, say a muzzle brake when fitting a suppressor, the old part is
// first moved into the player's kit; only once that has worked is the new one
// requested. If there's no room for the old part, nothing changes.
//
// The new part is asked for with the same arsenal request the game uses when an
// item is dragged onto a weapon, so the server still checks the storage belongs to
// the player, the distance, the arsenal and the supply cost. Magazines and
// everything else keep the vanilla behaviour.
modded class SCR_InventoryMenuUI
{
	protected ref OYB_FitAfterRemovalCallback m_OYB_FitCallback;

	override protected bool MoveBetweenFromVicinity_VirtualArsenal()
	{
		if (OYB_FitToHeldWeapon())
			return true;

		return super.MoveBetweenFromVicinity_VirtualArsenal();
	}

	override protected bool EquipDraggedItem_VirtualArsenal(bool forceEquip = false)
	{
		if (OYB_FitToHeldWeapon())
			return true;

		return super.EquipDraggedItem_VirtualArsenal(forceEquip);
	}

	protected bool OYB_FitToHeldWeapon()
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent())
			return false;

		IEntity item = arsenalSlot.GetInventoryItemComponent().GetOwner();
		if (!item || item.FindComponent(BaseMagazineComponent))
			return false;

		IEntity weaponEntity = OYB_GetHeldWeaponEntity();
		if (!weaponEntity)
			return false;

		BaseWeaponComponent weapon = BaseWeaponComponent.Cast(weaponEntity.FindComponent(BaseWeaponComponent));
		WeaponAttachmentsStorageComponent weaponStorage = WeaponAttachmentsStorageComponent.Cast(weaponEntity.FindComponent(WeaponAttachmentsStorageComponent));
		if (!weapon || !weaponStorage)
			return false;

		array<AttachmentSlotComponent> slots = {};
		weapon.GetAttachments(slots);
		AttachmentSlotComponent target;
		foreach (AttachmentSlotComponent slot : slots)
		{
			if (slot && slot.CanSetAttachment(item))
			{
				target = slot;
				break;
			}
		}
		if (!target)
			return false;

		IEntity arsenalEntity = SCR_InventoryStorageBaseUI.ARSENAL_SLOT_STORAGES.Get(arsenalSlot);
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(arsenalEntity);
		if (!resourceComponent)
			return false;

		RplId resourceId = Replication.FindItemId(resourceComponent);
		RplId storageId = Replication.FindItemId(weaponStorage);
		ResourceName resource = arsenalSlot.GetItemResource();

		IEntity fitted = target.GetAttachedEntity();
		if (!fitted)
		{
			OYB_RequestFromArsenal(resourceId, storageId, resource);
			return true;
		}

		BaseInventoryStorageComponent keep = m_InventoryManager.FindStorageForItem(fitted, EStoragePurpose.PURPOSE_DEPOSIT);
		if (!keep)
		{
			Print("OYB_ARSENAL no room in the player's kit for the part being replaced, nothing changed");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
			return true;
		}

		m_OYB_FitCallback = new OYB_FitAfterRemovalCallback();
		m_OYB_FitCallback.m_ResourceId = resourceId;
		m_OYB_FitCallback.m_StorageId = storageId;
		m_OYB_FitCallback.m_Resource = resource;

		if (!m_InventoryManager.TryMoveItemToStorage(fitted, keep, -1, m_OYB_FitCallback))
		{
			Print("OYB_ARSENAL the part being replaced couldn't be moved, nothing changed");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
		}
		return true;
	}

	protected IEntity OYB_GetHeldWeaponEntity()
	{
		if (!m_Player)
			return null;

		BaseWeaponManagerComponent weapons = BaseWeaponManagerComponent.Cast(m_Player.FindComponent(BaseWeaponManagerComponent));
		if (!weapons)
			return null;

		BaseWeaponComponent weapon = weapons.GetCurrentWeapon();
		if (!weapon)
			return null;

		WeaponSlotComponent slot = WeaponSlotComponent.Cast(weapon);
		if (slot)
			return slot.GetWeaponEntity();

		return weapon.GetOwner();
	}

	static void OYB_RequestFromArsenal(RplId resourceId, RplId storageId, ResourceName resource)
	{
		SCR_ResourcePlayerControllerInventoryComponent requests = SCR_ResourcePlayerControllerInventoryComponent.Cast(GetGame().GetPlayerController().FindComponent(SCR_ResourcePlayerControllerInventoryComponent));
		if (requests)
			requests.RpcAsk_ArsenalRequestItem(resourceId, storageId, resource, EResourceType.SUPPLIES);
	}
}

class OYB_FitAfterRemovalCallback : ScriptedInventoryOperationCallback
{
	RplId m_ResourceId;
	RplId m_StorageId;
	ResourceName m_Resource;

	override protected void OnComplete()
	{
		SCR_InventoryMenuUI.OYB_RequestFromArsenal(m_ResourceId, m_StorageId, m_Resource);
	}

	override protected void OnFailed()
	{
		Print("OYB_ARSENAL the part being replaced couldn't be moved, nothing was fitted");
	}
}
