import datetime
import logging
import random

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic
from django.db import models, transaction

from stark import config
from stark.apps.anima.constants import PLAYER_STATUSES, MESSAGE_TYPES, MOB_TYPES
from stark.apps.world.models import Room, RoomConnector, ItemInstance, Weapon, Equipment, Misc

MOVE_COST = 2 #TODO: move to global config

class Anima(models.Model):
    # name should be unique for player subclass (not enforced @ db level)
    name = models.CharField(max_length=40)
    room = models.ForeignKey(Room, related_name="%(class)s_related")
    level = models.IntegerField(default=1)
    experience = models.IntegerField(default=1)
    
    messages =  generic.GenericRelation('Message')
    
    hp = models.IntegerField(default=10)
    max_hp = models.IntegerField(default=10)    
    mp = models.IntegerField(default=10)
    max_mp = models.IntegerField(default=10)    
    sp = models.IntegerField(default=10)
    max_sp = models.IntegerField(default=10)
    
    main_hand = models.ForeignKey(ItemInstance,
                                  related_name="%(class)s_mainhand",
                                  blank=True, null=True)
    
    target_type = models.ForeignKey(ContentType, blank=True, null=True)
    target_id = models.PositiveIntegerField(blank=True, null=True)
    target = generic.GenericForeignKey('target_type', 'target_id')
    
    eq_head = models.ForeignKey(ItemInstance,
                                related_name="%(class)s_head",
                                blank=True, null=True)
    
    eq_chest = models.ForeignKey(ItemInstance,
                                 related_name="%(class)s_chest",
                                 blank=True, null=True)
    
    eq_arms = models.ForeignKey(ItemInstance,
                                related_name="%(class)s_arms",
                                blank=True, null=True)
    
    eq_legs = models.ForeignKey(ItemInstance,
                                related_name="%(class)s_legs",
                                blank=True, null=True)

    eq_feet = models.ForeignKey(ItemInstance,
                                related_name="%(class)s_feet",
                                blank=True, null=True)
    
    class Meta:
        abstract = True

    def equipment(self):
        eq = []
        
        for attr in Anima.__dict__.keys():
            if (attr[0:3] == 'eq_' or attr == 'main_hand') and getattr(self, attr, None):
                eq.append(getattr(self, attr))
        return eq

    """
    def inventory(self):
        #eq = self.equipment()
        inv = []
        for item in ItemInstance.objects.owned_by(self):
            # way 1
            if item.base.__class__.__name__ == 'Weapon':
                slot = main_hand
            elif item.base.__class__.__name__ == 'Equipment':
                slot = item.base.slot
            
            
            
            if hasattr(item.base, 'slot'):# and getattr(self, item.base.slot, None):
                print slot
                inv.append(item)
            # way 2
            #if item not in eq:
            #    inv.append(item)
        return inv
    """

    def notify(self, msg):
        Message.objects.create(type='notification',
                               destination=self.name,
                               content=msg)
    
    def move(self, xpos=None, ypos=None, to_room=None, random=False):
        
        # get the connector based on the input provided
        if xpos and ypos:
            try:
                connector = RoomConnector.objects.get(from_room=self.room,
                                                      to_room__xpos=xpos,
                                                      to_room__ypos=ypos)
            except RoomConnector.DoesNotExist:
                self.notify("You cannot go that way.")
                raise Exception("No connector exists to provied x/y coords")
        elif to_room:
            try:
                connector = RoomConnector.objects.get(from_room=self.room,
                                                      to_room=to_room)
            except RoomConnector.DoesNotExist:
                self.notify("You cannot go that way.")
                raise Exception("No connector exists to provided to_room")
        elif random:
            try:
                connector = RoomConnector.objects\
                            .filter(from_room=self.room)\
                            .order_by('?')[0]
            except RoomConnector.DoesNotExist:
                raise Exception("No exit out of this room exists")
        else:
            raise Exception('provide either x/y coords or set random=True')

        to_room = connector.to_room

        # unless in builder mode, check for move points and deduct points
        if not (self.__class__.__name__ == 'Mob' or hasattr(self, 'builder_mode') and self.builder_mode == True):
            if self.mp < MOVE_COST:
                msg = "Not enough movement points to move."
                self.notify(msg)
                raise Exception(msg)
            else:
                self.mp -= MOVE_COST

        # save where the user was before the move
        from_room = self.room
        
        # move the user
        self.room = to_room

        # save
        self.save()
        
        # tell every player in the room the anima was in that it's gone
        for player in Player.objects.filter(room=from_room, status='logged_in'):
            player.notify("%s leaves %s." % (self.name, connector.direction))
        
        rev_direction = None
        if connector.direction == 'north': rev_direction = 'south'
        elif connector.direction == 'east': rev_direction = 'west'
        elif connector.direction == 'south': rev_direction = 'north'
        elif connector.direction == 'west': rev_direction = 'east'
            
        # tell every player in the room the anima is moving to that it's
        # arrived, or simply that it's moved if the observer is the one
        # performing the action
        for player in Player.objects.filter(room=to_room, status='logged_in'):
            if player == self:
                self.notify("You leave %s" % connector.direction)
            else:
                player.notify("%s has arrived from the %s." %
                                (self.name, rev_direction))                
    
    def regen(self, attribute, points):
        """
        Regen player attribtue (hp, mp or sp) by x points
        """
        max = getattr(self, 'max_' + attribute)
        new = getattr(self, attribute) + points
        if new > max:
            new = max
        setattr(self, attribute, new)
        self.save()
    
    @transaction.commit_on_success
    def attack(self):        
        # make sure source and target are in the same room
        if self.target.room != self.room:
            self.target = None
            return
        
        # if the target is not engaged on something else, source becomes
        # target's target
        if not self.target.target:
            self.target.target = self
            self.target.save()
        
        # base dmg
        damage = 1
        
        attack_msg = ['hit', 'hits']

        # take the weapon into account
        if self.main_hand:
            rolls = self.main_hand.base.num_dice
            max = self.main_hand.base.num_faces
            damage += rolls * random.randint(1, max)
             
            if self.main_hand.base.weapon_class == 'short_blade':
                attack_msg = ['stab', 'stabs']
            if self.main_hand.base.weapon_class in ('medium_blade',
                                               'long_blade'):
                attack_msg = ['slash', 'slashes']
            elif self.main_hand.base.weapon_class == 'spear':
                attack_msg = ['strike', 'strikes']
            elif self.main_hand.base.weapon_class == 'chain':
                attack_msg = ['whip', 'whips']
            elif self.main_hand.base.weapon_class == 'projectile':
                attack_msg = ['throw', 'throws']
            elif self.main_hand.base.weapon_class == 'axe':
                attack_msg = ['hack', 'hacks']

        # record the hit
        self.target.hp -= damage
        self.target.save()

        # source notification (if player)
        if self.__class__.__name__ == "Player":
            self.notify("[%s dmg dealt] You %s %s!" % (
                            damage,
                            attack_msg[0],
                            self.target.name))
        # target notification (if player)
        if self.target.__class__.__name__ == "Player":
            self.target.notify("[%s dmg taken] %s %s you!" % (
                            damage,
                            self.name,
                            attack_msg[1]))
        # room notifications
        for room_player in self.room.player_related.all():
            if room_player not in (self, self.target):
                room_player.notify("%s %s %s!" % (
                                    self.name,
                                    attack_msg[1],
                                    self.target.name))

        # kill the target if applicable
        if self.target.hp <= 0:
            # if the target was a mob, adjust experience
            if self.target.__class__.__name__ == "Mob":
                self.experience += self.target.experience
            # kill the target anima, reset the target, save
            self.target.die()
            self.target = None
            self.save()

    @transaction.commit_on_success
    def die(self):
        # set hp back to full (for now)
        self.hp = 10
        
        self.target = None # useful if it's a player
        self.save()

        # notifications
        if self.__class__.__name__ == 'Player':
            self.notify("You are dead! Sorry...")        
        for room_player in self.room.player_related.all():
            if room_player != self:
                room_player.notify("%s is dead!" % self.name)

        # create a corpse
        corpse = Misc.objects.create(name="The corpse of %s" % self.name)
        ItemInstance.objects.create(base=corpse, owner=self.room)

        # if it's a player, move to the death room
        if self.__class__.__name__ == 'Mob':
            self.delete()
        elif self.__class__.__name__ == 'Player':
            death_room_id = getattr(config, 'DEATH_ROOM_ID', 1)
            try:
                death_room = Room.objects.get(pk=death_room_id)
            except Room.DoesNotExist:
                raise Exception("There must be a room with pk=1")
            self.room = death_room
            self.save()
    
    def give_item(self, item, give_to):
        
        if item.owner.room != give_to.room:
            stark_log = logging.getLogger('StarkLogger')
            message = "%s can't give item %s to %s because they aren't in the same room" % \
                      (self.name, item.name, give_to.name)
            stark_log.debug(message)
            raise Exception(message)

        item.owner = give_to
        item.save()

        for player in self.room.player_related.all():
            if player == self:
                self.notify("You give %s to %s." % \
                            (item.base.name, give_to.name))
            elif player == give_to:
                give_to.notify("%s gives you %s." % \
                               (self.name, item.base.name))
            else:
                player.notify("%s gives %s to %s" % \
                              (self.name, item.base.name, give_to.name))

    def drop_item(self, item):
        item.owner = self.room
        item.save()
        for player in self.room.player_related.all():
            if player == self:
                self.notify("You drop %s." % item.base.name)
            else:
                self.player("%s drops %s." % (self.name, item.base.name))

    def get_item(self, item):
        if item.owner != self.room:
            stark_log = logging.getLogger('StarkLogger')
            message = "%s can't get item %s because they're in different rooms" % \
                      (self.name, item.name)
            stark_log.debug(message)
            raise Exception(message)

        item.owner = self
        item.save()
        
        for player in self.room.player_related.all():
            if player == self:
                self.notify("You get %s." % item.base.name)
            else:
                player.notify("%s gets %s." % (self.name, item.base.name))

    def get_item_from_container(self, item):
        # container is in player or in player's room
        if (item.owner.owner.__class__ is Room and item.owner.owner == self.room) or \
           (item.owner.owner.__class__ is Player and item.owner.owner == self):
                        
            for player in self.room.player_related.all():
                if player == self:
                    self.notify("You get %s from %s." % \
                                (item.base.name, item.owner.base.name))
                else:
                    player.notify("%s gets %s from %s." % \
                                  (self.name, item.base.name, item.owner.base.name))

            item.owner = self
            item.save()

        else:
            stark_log = logging.getLogger('StarkLogger')
            message = "%s can't get item %s from container %s because they're in different rooms" % \
                      (self.name, item.name, item.owner.name)
            stark_log.debug(message)
            raise Exception(message)

    def put_item_in_container(self, item, container):
        # container is in the room
        if (container.owner.__class__ is Room and container.owner == item.owner.room) or \
           (container.owner.__class__ is Player and container.owner == item.owner):
            
                for player in self.room.player_related.all():
                    if player == self:
                        self.notify("You put %s in %s." % \
                                    (item.base.name, container.base.name))
                    else:
                        player.notify("%s puts %s in %s." % \
                            (self.name, item.base.name, container.base.name))
            
                item.owner = container
                item.save()

        else:
            stark_log = logging.getLogger('StarkLogger')
            message = "%s can't put item %s in container %s because they're in different rooms" % \
                      (self.name, item.base.name, container.base.name)
            stark_log.debug(message)
            raise Exception(message)


    def wear(self, item, wear_verb='wear'):
        if item.base.__class__.__name__ == "Weapon":
            slot = 'main_hand'
            if wear_verb == 'wear':
                wear_verb = 'wield'
        elif item.base.__class__.__name__ == "Equipment":
            slot = item.base.slot
        
        if getattr(self, slot):
            self.notify("You're already wearing something on this slot.")
            raise Exception("Slot occupied")
        
        setattr(self, slot, item)
        self.save()
        
        for player in self.room.player_related.all():
            if player == self:
                self.notify("You %s %s." % (wear_verb, item.base.name))
            else:
                player.notify("%s %ss %s." % (self.name, wear_verb, item.base.name))

    def remove(self, item):
        if item.base.__class__.__name__ == "Weapon":
            slot = 'main_hand'
        elif item.base.__class__.__name__ == "Equipment":
            slot = item.base.slot
        
        if not getattr(self, slot):
            self.notify("This slot is empty.")
            raise Exception("Slot empty")
            
        setattr(self, slot, None)
        self.save()
        
        for player in self.room.player_related.all():
            if player == self:
                self.notify("You remove %s." % item.base.name)
            else:
                player.notify("%s removes %s." % (self.name, item.base.name))

    def old_wear(self, item, slot=None, remove_msg=None, error_msg=None, wear_msg=None):

        # if the item passed is null, empty the specified slot
        previously_worn_item = getattr(self, slot)
        if not item: # remove the item
            setattr(self, slot, None)
            self.save()
            if not remove_msg:
                remove_msg = "You remove %s" % previously_worn_item.base.name
            self.notify(remove_msg)
        
        # an item is already on that slot
        elif getattr(self, slot): # something already on that slot
            if not error_msg:
                error_msg = "You are already wearing something on your %s" \
                            % item.base.name
            self.notify(error_msg)
        
        # we're good to go, wear the item
        else:
            setattr(self, slot, item)
            self.save()
            if not wear_msg:
                wear_msg = "You wear %s" % item.base.name
            self.notify(wear_msg)

    """
    def wield(self, weapon):
        if weapon:
            wear_msg = "You wield %s" % weapon.base.name
        else:
            wear_msg = None
        
        self.wear(weapon, 'main_hand', wear_msg=wear_msg)
    """

    def engage(self, target_type, target_id):
        not_here = "No-one by that name."
        try:
            target_type = ContentType.objects.get(model=target_type)
            target = target_type.model_class().objects.get(pk=target_id)
            if self.room != target.room:
                self.notify(not_here)

            self.target = target
            self.save()
        except Exception:
            self.notify(not_here)

    def save(self, *args, **kwargs):
        if not hasattr(self, 'room'):
            try:
                room = Room.objects.get(pk=1)
                self.room = room
            except Room.DoesNotExist:
                raise Exception("There needs to be a Room object with pk = 1")
        super(Anima, self).save(*args, **kwargs)
        return self

    def __unicode__(self):
        return u"%s" % self.name


class Player(Anima):
    user = models.ForeignKey(User, related_name='players')
    builder_mode = models.BooleanField(default=False)
    temporary = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=PLAYER_STATUSES)
    last_activity = models.DateTimeField(blank=True, null=True)
    
    def save(self, *args, **kwargs):
        self.last_activity = datetime.datetime.now()
        return super(Player, self).save(*args, **kwargs)

class Mob(Anima):
    # static mobs can't move
    static = models.BooleanField(default=False)

class Message(models.Model):
    created = models.DateTimeField(default=datetime.datetime.now(), blank=False)
    type = models.CharField(max_length=20, choices=MESSAGE_TYPES, blank=False)
    content = models.TextField(blank=False)
    destination = models.CharField(max_length=40, blank=True)

    content_type = models.ForeignKey(ContentType, blank=True, null=True)
    object_id = models.PositiveIntegerField(blank=True, null=True)
    author = generic.GenericForeignKey('content_type', 'object_id')
    
    def __init__(self, *args, **kwargs):
        super(Message, self).__init__(*args, **kwargs)
        self.created = datetime.datetime.now()
    
    def __unicode__(self):
        return u"%s: %s" % (self.author, self.content)