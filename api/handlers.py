import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.db.models import Q

from piston.handler import BaseHandler
from piston.authentication import HttpBasicAuthentication
from piston.utils import rc

from stark.apps.world.models import Room, RoomConnector, ItemInstance, Weapon
from stark.apps.anima.models import Player, Mob, Message

ROOM_RANGE = 10


def get_item_api(item):
    contains = []
    if item.base.capacity:
        for contained_item in item.owns.all():
            contains.append({
                'id': contained_item.id,
                'type': contained_item.base_type.name,
                'name': contained_item.base.name,
            })
            
    return {
        'id': item.id,
        'type': item.base_type.name,
        'name': item.base.name,
        'contains': contains,
        'capacity': item.base.capacity,
        'owner_type': item.owner_type.name,
        'owner_id': item.owner_id
    }

class ItemHandler(BaseHandler):
    allowed_methods = ('GET', 'PUT')
    model = ItemInstance

    def read(self, request, id):
        item = ItemInstance.objects.get(pk=id)
        
        return get_item_api(item)

    def update(self, request, id):
        item = ItemInstance.objects.get(pk=id)
        player = Player.objects.get(status='logged_in', user=request.user)

        # update item owner
        try:
            new_owner = ContentType.objects.get(model=request.PUT['owner_type']).model_class().objects.get(pk=request.POST['owner_id'])

            # player to player
            if item.owner.__class__ is Player and new_owner.__class__ is Player:
                player.give_item(item, new_owner)

            # player to room
            elif item.owner.__class__ is Player and new_owner.__class__ is Room:
                player.drop_item(item)

            # room to player
            elif item.owner.__class__ is Room and new_owner.__class__ is Player:
                player.get_item(item)

            # container to player
            elif item.owner.__class__ is ItemInstance and new_owner.__class__ is Player:
                player.get_item_from_container(item)

            # player to container
            elif item.owner.__class__ is Player and new_owner.__class__ is ItemInstance:
                player.put_item_in_container(item, new_owner)

            elif player.builder_mode:
                item.owner = new_owner
                item.save()

            else:
                return rc.FORBIDDEN

        except Exception, e:
            print e
            response = rc.BAD_REQUEST
            response.write(": %s" % e)
            return response

        return get_item_api(item)


class RoomHandler(BaseHandler):
    allowed_methods = ('GET', 'POST', 'PUT', 'DELETE')
    model = Room
    fields = ('id', 'title', 'description', 'xpos', 'ypos', 'type', 'north', 'east', 'south', 'west',
              ('player_related', ('id', 'name')),
              ('mob_related', ('id', 'name')),
              'items',
              )

    def create(self, request, *args, **kwargs):
        if not Player.objects.get(user=request.user, status='logged_in').builder_mode:
            return rc.FORBIDDEN
        
        if Room.objects.filter(xpos=request.POST['xpos'], ypos=request.POST['ypos']):
            return rc.BAD_REQUEST

        room = Room.objects.create(xpos=request.POST['xpos'], ypos=request.POST['ypos'], title='Untitled Room', type='field')
        
        return room
    
    def update(self, request, *args, **kwargs):
        try:
            # only a builder can change a room
            if not Player.objects.get(user=request.user, status='logged_in').builder_mode:
                return rc.FORBIDDEN
            
            from_room = Room.objects.get(pk=kwargs['id'])
            
            # if a connector has been passed, toggle accordingly, creating a
            # new empty room if need be
            for k in request.PUT.keys():
                
                def __connect_to(k):
                    if k == 'north': x, y = (0, -1)
                    elif k == 'east': x, y = (1, 0)
                    elif k == 'south': x, y = (0, 1)
                    elif k == 'west': x, y = (-1, 0)
                    to_x = from_room.xpos + x
                    to_y = from_room.ypos + y
                    
                    try:
                        to_room = Room.objects.get(xpos=to_x, ypos=to_y)
                    except Room.DoesNotExist:
                        return rc.BAD_REQUEST
                    
                    try:
                        connector = RoomConnector.objects.get(from_room=from_room, to_room=to_room)
                        connector.delete()
                    except RoomConnector.DoesNotExist:
                        connector = RoomConnector()
                        connector.from_room = from_room
                        connector.to_room = to_room
                        connector.direction = k
                        connector.type = 'Normal'
                        connector.save()

                if k in ('north', 'east', 'south', 'west'):
                    if request.PUT[k] == 'toggle':
                        __connect_to(k)

            super(RoomHandler, self).update(request, *args, **kwargs)
            return Room.objects.get(id=kwargs['id'])
        except Exception, e:
            print "Error: %s" % e
            return rc.BAD_REQUEST
        
    def delete(self, request, id):
        player = Player.objects.get(user=request.user, status='logged_in')
        room = Room.objects.get(pk=id)
        
        if player.room == room:
            # if possible, move the player to an adjacent room, otherwise to
            # the origin
            adjacent_rooms = room.from_room.all()
            if adjacent_rooms:
                player.room = adjacent_rooms[0].to_room
            else:
                player.room = Room.objects.get(pk=1)
            player.save()
            
        room.delete()
        
        return rc.DELETED

    @classmethod
    def items(self, room):
        result = []
        for i in ItemInstance.objects.filter(owner_type__name='room', owner_id=room.id):
            result.append(get_item_api(i))
        return result

    @classmethod
    def __determine_connection(self, room, direction):
        try:
            return room.from_room.get(direction=direction).type
        except RoomConnector.DoesNotExist:
            return None
        
        try:
            to_room = Room.objects.get(xpos=room.xpos + delta['x'], ypos = room.ypos + delta['y'])
            try:
                return RoomConnector.objects.get(from_room=room, to_room=to_room).type
            except RoomConnector.DoesNotExist:
                return None
        except Room.DoesNotExist:
            return None

    @classmethod
    def north(self, room): return self.__determine_connection(room, 'north')
    
    @classmethod
    def east(self, room): return self.__determine_connection(room, 'east')

    @classmethod
    def south(self, room): return self.__determine_connection(room, 'south')

    @classmethod
    def west(self, room): return self.__determine_connection(room, 'west')

class MapHandler(BaseHandler):
    allowed_methods = ('GET',)

    def read(self, request):
        if request.user.is_authenticated():
            player = Player.objects.get(user=request.user, status='logged_in')
            rooms = []
            min_x = None
            min_y = None
            
            for room in Room.objects.filter(
                            xpos__gt=player.room.xpos - ROOM_RANGE,
                            xpos__lt=player.room.xpos + ROOM_RANGE,
                            ypos__gt=player.room.ypos - ROOM_RANGE,
                            ypos__lt=player.room.ypos + ROOM_RANGE):
                
                # keep track of the lowest x
                if not min_x: min_x = room.xpos
                elif room.xpos < min_x: min_x = room.xpos
                
                # keep track of the lowest y
                if not min_y: min_y = room.ypos
                elif room.ypos < min_y: min_y = room.ypos
                
                # Because django-piston automatically looks up objects if
                # they've been previously defined as a handler's model (which
                # is the case for Room), I specifically fetch the room
                # attributes I need to speed up this api call.
                # If you let piston do this for you it's 5 to 10 times slower
                room_dict = {
                    'xpos': room.xpos,
                    'ypos': room.ypos,
                    'type': room.type,
                }
                
                for connection in room.from_room.all():
                    room_dict[connection.direction] = connection.type
                
                rooms.append(room_dict)

        else:
            return rc.FORBIDDEN
        
        return {
            'starting_point': {'xpos': min_x, 'ypos': min_y},
            'player_position': {'xpos': player.room.xpos, 'ypos': player.room.ypos},
            'rooms': rooms,
        }
    

class PlayerHandler(BaseHandler):
    allowed_methods = ('GET', 'PUT')
    model = Player
    fields = (
        'id',
        'name',
        'level',
        'builder_mode',
        'room',
        'items',
        'mp',
        'max_mp',
        'main_hand',
    )

    @classmethod
    def main_hand(self, player):
        return get_item_api(player.main_hand)

    def read(self, request, id=None):
        if id == 'me':
            player = Player.objects.get(user=request.user, status='logged_in')
        else:
            return RC.FORBIDDEN
            #player = Player.objects.get(pk=id) # TODO: this should probably return some kind of error instead
        return player
        
    def update(self, request, id=None):
        player = Player.objects.get(user=request.user, status='logged_in')

        if request.PUT.has_key('xpos') and request.PUT.has_key('ypos'):
            x = int(request.PUT['xpos'])
            y = int(request.PUT['ypos'])
            from_room = player.room
            try:
                
                # builders can go directly to any room
                if player.builder_mode:
                    player.room = Room.objects.get(xpos=x, ypos=y)
                    player.save()
                    return player
                
                # see if there is a path to the room
                try:
                    connector = RoomConnector.objects.get(
                                            from_room=from_room,
                                            to_room__xpos=x,
                                            to_room__ypos=y)
                except RoomConnector.DoesNotExist:
                    raise Exception("you cannot go that way.")
                
                player.move(connector.to_room)

            except Exception, e:
                print e
                response = rc.BAD_REQUEST
                response.write(": %s" % e)
                return response

        if request.PUT.has_key('target_type') and request.PUT.has_key('target_id'):
            try:
                player.engage(request.PUT['target_type'], request.PUT['target_id'])
            except Exception, e:
                print e
                response = rc.BAD_REQUEST
                response.write(": %s" % e)
                return response

        print request.PUT

        if request.PUT.get('main_hand'):
            player.wield(ItemInstance.objects.get(pk=request.PUT['main_hand']))

        return player

    @classmethod
    def items(self, player):
        # refer to RoomHandler.items for explanation for the weirdness below
        result = []
        for i in ItemInstance.objects.filter(owner_type__name='player', owner_id=player.id):
            result.append(get_item_api(i))
        return result        

class MessageHandler(BaseHandler):
    allowed_methods = ('GET', 'POST')
    model = Message
    fields = ('content', 'type', 'created', 'source')
    
    def read(self, request, *args, **kwargs):
        
        player = Player.objects.get(user=request.user, status='logged_in') 
        
        messages = []

        for message in Message.objects.filter(type='chat').order_by('created')[:10]:
            messages.append(message)

        for message in Message.objects.filter(type='notification', destination=player.name).order_by('created'):
            messages.append(message)

        messages = sorted(messages, lambda x, y: cmp(x.created, y.created))
        return messages
    
    def create(self, request, *args, **kwargs):
        try:
            content = request.POST['content']
            type = request.POST['type']
            message = Message.objects.create(
                created=datetime.datetime.now(),
                type=type,
                content=content,
                author=Player.objects.get(user=request.user, status='logged_in'),
            )
            return message
        except Exception, e:
            print u"exception: %s" % e
            return rc.BAD_REQUEST
    
    @classmethod
    def source(self, message):
        if message.author:
            return u"%s" % message.author.name
        else:
            return u"system"

class PingHandler(BaseHandler):
    allowed_methods = ('GET',)
    
    def read(self, request):
        response = rc.BAD_REQUEST
        response.write("some error")
        return response
        return {'status': 'OK'}