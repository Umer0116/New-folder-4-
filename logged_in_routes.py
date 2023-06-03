from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect,APIRouter
import time,db
import json
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse 
from bson.objectid import ObjectId
import random
import sys
from fastapi import FastAPI, Body, Depends,Header
from app.auth.auth_bearer import JWTBearer
from app.auth.auth_handler import signJWT 
import jwt
from models import * 
from pymongo.collection import ReturnDocument
import logging 
from guest_routes import ConnectionStreamManager
import asyncio
from typing import Dict, List
import asyncio
from collections import defaultdict
import guest_routes as gr

router = APIRouter()   
#=========================================================
class Loggedin_LobbyManager:
    def __init__(self):
        self.Loggedin_active_connections:   List[WebSocket] = []
        self.Loggedin_disconnected_clients: List[WebSocket] = []
        self.Loggedin_lobbies:              dict[str, List[WebSocket]] = {}
    async def connect(self, websocket: WebSocket, Loggedin_lobby_id: str, token: str):
        try:
            decoded = jwt.decode(token.replace("Bearer ", ""), options={"verify_signature": False})
        except jwt.exceptions.InvalidSignatureError:
            return
        user_id = decoded.get("user_id")
        if not user_id or not db.Users.count_documents({"username": user_id}):
            return
        if websocket in self.Loggedin_disconnected_clients:
            self.Loggedin_disconnected_clients.remove(websocket)
        else:
            await websocket.accept()
        self.Loggedin_active_connections.append(websocket)
        if Loggedin_lobby_id not in self.Loggedin_lobbies:
            self.Loggedin_lobbies[Loggedin_lobby_id] = []
        self.Loggedin_lobbies[Loggedin_lobby_id].append(websocket)
    def disconnect(self, websocket: WebSocket):
        self.Loggedin_active_connections.remove(websocket)
        self.Loggedin_disconnected_clients.append(websocket)

        for Loggedin_lobby_id, clients in self.Loggedin_lobbies.items():
            if websocket in clients:
                clients.remove(websocket)
    async def send_personal_message(self, message: str, websocket: WebSocket):
        if websocket in self.Loggedin_active_connections:
            await websocket.send_json(message)
    async def broadcast(self, message: str):
        for connection in self.Loggedin_active_connections:
            await connection.send_json(message) 
  
    async def broadcast_to_lobby(self, lobby_id: str, message: str):
        clients = self.Loggedin_lobbies.get(lobby_id)
        if not clients:
            return
        for client in clients:
            try:
                await client.send_json(message)
            except RuntimeError as e:
                if "Connection is closed" in str(e):
                    continue
                else:
                    raise e
#====================================================================
async def Loggedin_get_categories():
    Loggedin_categories = list(db.Catagories.find({}, {"_id": 0,"key": 0}))
    return Loggedin_categories
async def Loggedin_get_lobbies():
    Loggedin_Lobbies = list(db.loggedin_Virtual_Lobbies.find({}, {"_id": 0}))
    return Loggedin_Lobbies
#====================================================================
Loggedin_stream_manager  = ConnectionStreamManager() 
Loggedin_Lobby_Manager   = Loggedin_LobbyManager()
#=========================================================
def Loggedin_username_exists(data, username): 
    for element in data:
        if element['username'] == username:
            return True
    return False
def Loggedin_username_exists_inList(data, username):
    if username in data:
        return True
    else:
        return False
def count_voted_users(Loggedin_lobby_id): 
    lobby = db.loggedin_Virtual_Lobbies.find_one({"lobby_id": Loggedin_lobby_id})
    if lobby is None:
        return {"voted_users": 0, "total_players": 0}
    voted_users = sum([user["vote_rcvd"] for user in lobby["data"]])
    total_players = len(lobby["players"])
    return {"voted_users": voted_users, "total_players": total_players}
async def get_photo_url(username): 
    result = db.Users.find_one({"username": username}) 
    if result:
        photo_url = result["photo_url"]
        return photo_url
    else:
        return None
    
async def lobbyIDget(index: int, UserID: str, category: str , catdic: dict):
    
    cat_details_data          = catdic 
    topic_array = await gr.get_topics_by_category(category)
    Loggedin_Lobbies          = await Loggedin_get_lobbies()
    min_players = int(cat_details_data[index]['player']['min_players'])     # Getting the min allowed players from the cartegory details 
    max_players = int(cat_details_data[index]['player']['max_players'])     # Getting the max allowed players from the category details  
    if cat_details_data[index]['catagory_name'] != category:
        return {"status" : False, "code":900 , "message":"something is wrong with your sent data!"}
    cnt = 0     
    # if there is no lobby present at all then we will create one for the iteration
    if len(Loggedin_Lobbies) == 0:
        newLobbyObject = cat_details_data[index] 
        cmntdict = [{"username":UserID,"comment":" haven't commented yet!","vote_given":0,"vote_rcvd":0}]
        newLobbyObject['topics'] = random.choice(topic_array)
        mydict = {"lobby_id":"","started_at":0,"category_name":newLobbyObject['catagory_name'],
                                        "entry_fee":newLobbyObject['entry_fee'],
                                        "min_players":min_players,
                                        "max_players":max_players,
                                        "players":[],
                                        "timer":newLobbyObject['timer'],
                                        "topics":newLobbyObject['topics'],
                                        "data":cmntdict}
        writtenID         = db.loggedin_Virtual_Lobbies.insert_one(mydict).inserted_id 
        db.loggedin_Virtual_Lobbies.update_one({"_id": writtenID}, {"$set": {"lobby_id": str(writtenID)}})
        mydict["lobby_id"]=str(writtenID)
        del mydict['_id']
        del mydict['started_at']
        return {"status" : True, "code":200 , "message":mydict}  
    elif len(Loggedin_Lobbies) != 0:
        for lobby in Loggedin_Lobbies:
            # if there is a lobby available and the slots are available then put the user in that lobby and return that lobby data 
            if (lobby['category_name']) == category and  (lobby['started_at'] == 0 or time.time() - lobby['started_at'] < int(lobby['timer']['startup_timer'])-5): 
                checkuser =  Loggedin_username_exists(lobby['data'],UserID) 
                if checkuser == False: 
                    result = db.loggedin_Virtual_Lobbies.find_one_and_update(
                                                    {"lobby_id": lobby['lobby_id'], "$expr": {"$lt": [{"$size": "$data"}, max_players]}},
                                                    {"$push": {"data": {"username": UserID, "comment": " haven't commented yet!", "vote_given": 0, "vote_rcvd": 0}}},
                                                    return_document=ReturnDocument.AFTER,
                                                    projection={"_id": 0, "started_at": 0}
                                            )
                    return {"status" : True, "code":200 , "message":result} 
                else: 
                    del lobby['started_at']
                    return {"status" : True, "code":200 , "message":lobby} 
        #else: # else create a new lobby and put user in this new lobby and return this new lobby data 
        newLobbyObject = cat_details_data[index]
        cmntdict = [{"username":UserID,"comment":" haven't commented yet!","vote_given":0,"vote_rcvd":0}]
        newLobbyObject['topics'] = random.choice(topic_array)
        mydict = {"lobby_id":"","started_at":0,"category_name":newLobbyObject['catagory_name'],
                                                    "entry_fee":newLobbyObject['entry_fee'],
                                                    "min_players":min_players,
                                                    "max_players":max_players,
                                                    "players":[],
                                                    "timer":newLobbyObject['timer'],
                                                    "topics":newLobbyObject['topics'],
                                                    "data":cmntdict}
        writtenID         = db.loggedin_Virtual_Lobbies.insert_one(mydict).inserted_id 
        db.loggedin_Virtual_Lobbies.update_one({"_id": writtenID}, {"$set": {"lobby_id": str(writtenID)}})
        mydict["lobby_id"]=str(writtenID)
        del mydict['started_at']
        del mydict['_id']
        return {"status" : True, "code":200 , "message":mydict}  

async def get_index_by_category_name(category_name, game_list): 
    for i, game in enumerate(game_list):
        if game['catagory_name'] == category_name:
            return i
    return -1
async def comparefee(userID,cat):
    query = {"username": userID}
    document = db.Users.find_one(query)
    if document is not None: 
        earnings = document["stats"]["earnings"] 
        query = {"catagory_name": cat}
        matching_document = db.Catagories.find_one(query)
        fee_value = matching_document["entry_fee"]
        if float(earnings) >= float(fee_value):
            return True
        else:
            return False 
 # Get Lobby ID to join
@router.post("/User/GetLobbyID",tags=['[APP] Users Management'], dependencies=[Depends(JWTBearer())])
async def add_new_catagory(data: get_lobby ,  Authorization: str | None = Header(default=None)):
    token       =   Authorization.replace("Bearer ", "")
    decoded     =   jwt.decode(token, options={"verify_signature": False})
    user_id     =   decoded['user_id']
    if db.Users.count_documents({'username':  user_id}):
        enoughTokens = await comparefee(user_id,data.category) 
        if enoughTokens == False:
            return {"status":False, "code":404 , "message":"Insufficient Tokens"}
        cat_details_data1 = await gr.get_categories_with_empty_topics()#await Loggedin_get_categories()
        index = await get_index_by_category_name(data.category,cat_details_data1) 
        data = await lobbyIDget(index , user_id , data.category , cat_details_data1)
        if data['status'] == False:
            return data
        else:
            return data
    else:
        return {"status":False, "code":200 , "message":"User does not exist!"}
#==============================================================================================================
# This is to find the index of a particular lobby 
async def find_index(lst, id):
    for i, d in enumerate(lst):
        if d.get('lobby_id') == id:
            return i
    return -1
#==============================================================================================================
def count_commented_users(data):
    count = 0
    for user in data:
        if user["comment"] != " haven't commented yet!":
            count += 1
    return count

#==============================================================================================================
#============================================================================================================== 

async def deductFee(userID, fee):
    query = {"username": userID}
    document = db.Users.find_one(query)
    if document is not None: 
        earnings = document["stats"]["earnings"]
        new_earnings = earnings - fee 
        update_query = {"$set": {"stats.earnings": new_earnings}}
        db.Users.update_one(query, update_query)  
        return None    
async def reimburseFee(userID, fee):
    query = {"username": userID}
    document = db.Users.find_one(query)
    if document is not None: 
        earnings = document["stats"]["earnings"]
        new_earnings = earnings + fee 
        update_query = {"$set": {"stats.earnings": new_earnings}}
        db.Users.update_one(query, update_query)  
        return None   
# This is to join the lobby 
@router.websocket("/User/GameLobby/{lobbyID}/{token}")
async def lobby_endpoint(websocket: WebSocket , lobbyID: str , token:str):
    await Loggedin_Lobby_Manager.connect(websocket , lobbyID,token) 
    token       =   token.replace("Bearer ", "")
    decoded     =   jwt.decode(token, options={"verify_signature": False})
    userID      =   decoded['user_id']
    Loggedin_Lobbies = await Loggedin_get_lobbies() 
    index       =   await find_index(Loggedin_Lobbies,lobbyID)  
    checkuser   =   Loggedin_username_exists_inList(Loggedin_Lobbies[index]['players'],userID) 
    if checkuser == False:
        dataconnect = db.loggedin_Virtual_Lobbies.update_one({"lobby_id": lobbyID}, {"$push": {"players": userID}}) 
        Loggedin_Lobbies[index]['players'].append(userID) 
        feetoded = int(Loggedin_Lobbies[index]['entry_fee'])
        await deductFee(userID,feetoded) 
    participants = len(Loggedin_Lobbies[index]['players'])
    minLimit = Loggedin_Lobbies[index]['min_players']
    if participants < minLimit: 
        msg = {
                "status":True,
                "code":30,
                "existing_players":Loggedin_Lobbies[index]['players'],
                "timer":0
                } 
        msg["existing_players"] = [{"username": player, "photo_url": await get_photo_url(player)} for player in Loggedin_Lobbies[index]['players']] 
        print(msg)
        await Loggedin_Lobby_Manager.broadcast_to_lobby(lobbyID,msg) 
    if participants == minLimit: 
        current_time = int(time.time())
        db.loggedin_Virtual_Lobbies.update_one({"lobby_id": lobbyID}, {"$set": {"started_at": current_time}})
        msg = {
                "status":True,
                "code":30,
                "existing_players":Loggedin_Lobbies[index]['players'],
                "timer":current_time
                }  
        msg["existing_players"] = [{"username": player, "photo_url": await get_photo_url(player)} for player in Loggedin_Lobbies[index]['players']] 
        await Loggedin_Lobby_Manager.broadcast_to_lobby(lobbyID,msg) 

    elif participants > minLimit :
        current_time = int(time.time())
        document     = db.loggedin_Virtual_Lobbies.find_one({"lobby_id": lobbyID})
        started_at   = document["started_at"] 
        msg = {
                "status":True,
                "code":30,
                "existing_players":Loggedin_Lobbies[index]['players'],
                "timer":started_at
                } 
        msg["existing_players"] = [{"username": player, "photo_url": await get_photo_url(player)} for player in Loggedin_Lobbies[index]['players']] 
        await Loggedin_Lobby_Manager.broadcast_to_lobby(lobbyID,msg) 
    msg={} 
    try:
        while True:
            data = await websocket.receive_json()  
            if lobbyID != data['lobby_id']:
                msg = {
                        "status":False,
                        "code":900,
                        "type":"error",
                        "message" : "lobby id in connection is not the same as the lobby id in message!"
                        } 
                await Loggedin_Lobby_Manager.send_personal_message(msg ,websocket )   
            #for receiving the comments and updating them in the array
            elif (data['code']) == 40:
                #{"code": 40, "lobby_id":"asdadsasd","type": "comment", "message": "This is the user's comment"}
                index1       =   await find_index(Loggedin_Lobbies,lobbyID)  
                if index1 != -1:
                    sumitedComment = data['message'].replace('\n', '. ')  
                    updated_doc = db.loggedin_Virtual_Lobbies.find_one_and_update({"lobby_id": lobbyID, "data.username": userID}, 
                                                                            {"$set": {"data.$[elem].comment": sumitedComment}}, 
                                                                            return_document=ReturnDocument.AFTER,
                                                                            array_filters=[{"elem.username": userID}])
                    updated_data = updated_doc["data"]
                    commed_users = count_commented_users(updated_data)  
                    msg = {
                        "status":True,
                        "code":40,
                        "type":"NewComment",
                        "message" : updated_data,
                        "commented":commed_users
                        } 
                    await Loggedin_Lobby_Manager.broadcast_to_lobby(data['lobby_id'] , msg)
            #for receiving the votes and updating them in the array
            elif (data['code']) == 41:
                # {"code": 41, "lobby_id":"asdadsasd","type": "vote", "vote_for": "sdsf"}
                index2       =   await find_index(Loggedin_Lobbies,lobbyID)  
                if index2 != -1:
                    db.loggedin_Virtual_Lobbies.update_one({"lobby_id": lobbyID, "data.username": data['vote_for']}, {"$inc": {"data.$.vote_rcvd": 1}})
                    msg = {
                        "status":True,
                        "code":41,
                        "type":"vote",
                        "message" : count_voted_users(lobbyID)
                        } 
                    await Loggedin_Lobby_Manager.broadcast_to_lobby(data['lobby_id'] , msg)
            else:
                msg = {
                        "status":True,
                        "code":900,
                        "type":"error",
                        "message" : "something is wrong!"
                        } 
                await Loggedin_Lobby_Manager.send_personal_message(msg ,websocket )

    except WebSocketDisconnect: 
        Loggedin_Lobbies = await Loggedin_get_lobbies() 
        index = await find_index(Loggedin_Lobbies, lobbyID)
        if index != -1: 
            if userID in Loggedin_Lobbies[index]['players'] and Loggedin_Lobbies[index]['started_at'] == 0 :
                Loggedin_Lobbies[index]['players'].remove(userID) 
                db.loggedin_Virtual_Lobbies.update_one({"lobby_id": lobbyID},{"$pull": {"players": userID,"data": {"username": userID}}})
                document = db.loggedin_Virtual_Lobbies.find_one({"lobby_id": lobbyID}) 
                started_at1 = document["started_at"] 
                msg = {
                    "status": True,
                    "code": 30,
                    "existing_players": Loggedin_Lobbies[index]['players'],
                    "timer": started_at1
                }
                msg["existing_players"] = [{"username": player, "photo_url": await get_photo_url(player)} for player in Loggedin_Lobbies[index]['players']] 
                await reimburseFee(userID,int(Loggedin_Lobbies[index]['entry_fee'])) 
                Loggedin_Lobby_Manager.disconnect(websocket) 
                await Loggedin_Lobby_Manager.broadcast_to_lobby(lobbyID, msg) 
#=================================================================================================
def find_winner(results,  amount ):
    user_votes = {}
    num_voted = 0
    for i in results:
        user_votes[i['username']] = i['vote_rcvd']
        if i['vote_rcvd'] > 0:
            num_voted += i['vote_rcvd']
    max_vote = max(user_votes.values())
    if max_vote > 0:
        winners = [i for i in user_votes if user_votes[i] == max_vote]
    else:
        winners = []
    non_winners = [{"username":i, "votes": user_votes[i], "comment": [x for x in results if x["username"] == i][0]["comment"]} for i in user_votes if i not in winners]
    num_participants = len(winners) + len(non_winners)
    if len(winners) > 1: 
        prize = amount // len(winners)  
        return {"tie": True, "prize": prize, "winners": [{"username": i, "votes": user_votes[i], "comment": [x for x in results if x["username"] == i][0]["comment"]} for i in winners], "non_winners": non_winners, "num_participants": num_participants, "num_voted": num_voted}
    elif len(winners) == 1:
        return {"tie": False, "prize": amount, "winners": [{"username": winners[0], "votes": user_votes[winners[0]], "comment": [x for x in results if x["username"] == winners[0]][0]["comment"]}], "non_winners": non_winners, "num_participants": num_participants, "num_voted": num_voted}
    else:
        return {"tie": False, "prize": 0, "winners": [], "non_winners": non_winners, "num_participants": num_participants, "num_voted": num_voted}
def update_winner_earnings(results, prize):
    winners = results["winners"]
    for winner in winners:
        query = {"username": winner}
        update = {"$inc": {"stats.earnings": prize}}
        db.Users.update_one(query, update)
#=================================================================================================
async def add_photo_url(result_dict):
    for winner in result_dict["winners"]:
        photo_url = await get_photo_url(winner["username"])
        if photo_url:
            winner["photo_url"] = photo_url
    for non_winner in result_dict["non_winners"]:
        photo_url = await get_photo_url(non_winner["username"])
        if photo_url:
            non_winner["photo_url"] = photo_url
    return result_dict
@router.post("/User/GetResults",tags=['[APP] Users Management'], dependencies=[Depends(JWTBearer())])
async def get_results(data: get_results ,  Authorization: str | None = Header(default=None)):
    token       =   Authorization.replace("Bearer ", "")
    decoded     =   jwt.decode(token, options={"verify_signature": False})
    user_id     =   decoded['user_id']
    Loggedin_Lobbies          = await Loggedin_get_lobbies()  
    index3       =   await find_index(Loggedin_Lobbies,data.lobby_id)      
    if index3 != -1:
        if (Loggedin_Lobbies[index3]['lobby_id']) == data.lobby_id : 
            doc = Loggedin_Lobbies[index3]
            entryfee = Loggedin_Lobbies[index3]['entry_fee']  
            totalPlayers = len(Loggedin_Lobbies[index3]['players']) 
            lobbypool = (int(entryfee))*totalPlayers 
            currenttime = int(time.time())
            doc['time'] = currenttime 
            print(Loggedin_Lobbies[index3]['data'])
            result = await add_photo_url(find_winner(Loggedin_Lobbies[index3]['data'],lobbypool))
            update_winner_earnings(result, lobbypool)
            doc['game_result']=result 
            db.loggedin_Games_h.insert_one(doc)
            del Loggedin_Lobbies[index3] 
            done = db.loggedin_Virtual_Lobbies.delete_one({"_id": ObjectId(data.lobby_id)})
            return {"status":True, "code":200 , "results": result } 
            
    elif db.loggedin_Games_h.find_one({"lobby_id": data.lobby_id }):
        result = db.loggedin_Games_h.find_one({"lobby_id": data.lobby_id})
        del result['_id'] 
        entryfee = result['entry_fee']  
        totalPlayers = len(result['players']) 
        lobbypool = (int(entryfee))*totalPlayers 
        result1 =  await add_photo_url(find_winner(result['data'],lobbypool))
        return {"status":True, "code":200 , "results":result1}  
    else:
        return {"status":False, "code":900 , "message":"something is not right!"} 

  