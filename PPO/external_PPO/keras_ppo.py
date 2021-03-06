# Initial framework taken from https://github.com/jaara/AI-blog/blob/master/CartPole-A3C.py
import pandas
import numpy as np
import gym
from keras.models import Model
from keras.layers import Input, Dense, Dropout
from keras import backend as K
from keras.optimizers import Adam

import numba as nb
from tensorboardX import SummaryWriter

ENV = 'BipedalWalker-v2'
CONTINUOUS = True

EPISODES = 10000

LOSS_CLIPPING = 0.2 # Only implemented clipping for the surrogate loss, paper said it was best
EPOCHS = 10
NOISE = 1.0

GAMMA = 0.99

BATCH_SIZE = 512
NUM_ACTIONS = 4
NUM_STATE = 24
HIDDEN_SIZE = 256
ENTROPY_LOSS = 5 * 1e-3 # Does not converge without entropy penalty
LR = 1e-4 # Lower lr stabilises training greatly

DUMMY_ACTION, DUMMY_VALUE = np.zeros((1, NUM_ACTIONS)), np.zeros((1, 1))


@nb.jit
def exponential_average(old, new, b1):
    return old * b1 + (1-b1) * new


def proximal_policy_optimization_loss(advantage, old_prediction):
    def loss(y_true, y_pred):
        prob = K.sum(y_true * y_pred)
        old_prob = K.sum(y_true * old_prediction)
        r = prob/(old_prob + 1e-10)

        return -K.mean(K.minimum(r * advantage, K.clip(r, min_value=1 - LOSS_CLIPPING, max_value=1 + LOSS_CLIPPING) * advantage)) + ENTROPY_LOSS * (prob * K.log(prob + 1e-10))
    return loss


def proximal_policy_optimization_loss_continuous(advantage, old_prediction):
    def loss(y_true, y_pred):
        var = K.square(NOISE)
        pi = 3.1415926
        denom = K.sqrt(2 * pi * var)
        prob_num = K.exp(- K.square(y_true - y_pred) / (2 * var))
        old_prob_num = K.exp(- K.square(y_true - old_prediction) / (2 * var))

        prob = prob_num/denom
        old_prob = old_prob_num/denom
        r = prob/(old_prob + 1e-10)

        return -K.mean(K.minimum(r * advantage, K.clip(r, min_value=1 - LOSS_CLIPPING, max_value=1 + LOSS_CLIPPING) * advantage))
    return loss


class Agent:
    def __init__(self):
        self.critic = self.build_critic()
        if CONTINUOUS is False:
            self.actor = self.build_actor()
        else:
            self.actor = self.build_actor_continuous()

        self.env = gym.make(ENV)
        print(self.env.action_space, 'action_space', self.env.observation_space, 'observation_space')
        self.episode = 0
        self.observation = self.env.reset()
        self.val = False
        self.reward = []
        self.reward_over_time = []
        self.rews = []
        self.mean_100 = []
        self.writer = SummaryWriter('AllRuns/continuous/lunar_lander_v2_no_dropout')
        self.gradient_steps = 0

    def build_actor(self):

        state_input = Input(shape=(NUM_STATE,))
        advantage = Input(shape=(1,))
        old_prediction = Input(shape=(NUM_ACTIONS,))

        x = Dense(HIDDEN_SIZE, activation='relu')(state_input)
        x = Dropout(0.5)(x)
        x = Dense(HIDDEN_SIZE, activation='relu')(x)
        x = Dropout(0.5)(x)

        out_actions = Dense(NUM_ACTIONS, activation='softmax', name='output')(x)

        model = Model(inputs=[state_input, advantage, old_prediction], outputs=[out_actions])
        model.compile(optimizer=Adam(lr=LR),
                      loss=[proximal_policy_optimization_loss(
                          advantage=advantage,
                          old_prediction=old_prediction)])
        model.summary()

        return model

    def build_actor_continuous(self):
        state_input = Input(shape=(NUM_STATE,))
        advantage = Input(shape=(1,))
        old_prediction = Input(shape=(NUM_ACTIONS,))

        x = Dense(HIDDEN_SIZE, activation='relu')(state_input)
        x = Dense(HIDDEN_SIZE, activation='relu')(x)

        out_actions = Dense(NUM_ACTIONS, name='output', activation='tanh')(x)

        model = Model(inputs=[state_input, advantage, old_prediction], outputs=[out_actions])
        model.compile(optimizer=Adam(lr=LR),
                      loss=[proximal_policy_optimization_loss_continuous(
                          advantage=advantage,
                          old_prediction=old_prediction)])
        model.summary()

        return model

    def build_critic(self):

        state_input = Input(shape=(NUM_STATE,))
        x = Dense(HIDDEN_SIZE, activation='relu')(state_input)
        x = Dropout(0.5)(x)
        x = Dense(HIDDEN_SIZE, activation='relu')(x)
        x = Dropout(0.5)(x)

        out_value = Dense(1)(x)

        model = Model(inputs=[state_input], outputs=[out_value])
        model.compile(optimizer=Adam(lr=LR), loss='mse')

        return model

    def reset_env(self):
        self.episode += 1
        if self.episode % 100 == 0:
            self.val = True
        else:
            self.val = False
        self.observation = self.env.reset()
        self.reward = []

    def get_action(self):
        p = self.actor.predict([self.observation.reshape(1, NUM_STATE), DUMMY_VALUE, DUMMY_ACTION])
        if self.val is False:
            action = np.random.choice(NUM_ACTIONS, p=np.nan_to_num(p[0]))
        else:
            action = np.argmax(np.nan_to_num(p[0]))
        action_matrix = np.zeros(NUM_ACTIONS)
        action_matrix[action] = 1
        return action, action_matrix, p

    def get_action_continuous(self):
        p = self.actor.predict([self.observation.reshape(1, NUM_STATE), DUMMY_VALUE, DUMMY_ACTION])
        if self.val is False:
            action = action_matrix = p[0] + np.random.normal(loc=0, scale=NOISE, size=p[0].shape)
        else:
            action = action_matrix = p[0]
        return action, action_matrix, p

    def transform_reward(self):
        if self.val is True:
            self.writer.add_scalar('Val episode reward', np.array(self.reward).sum(), self.episode)
        else:
            self.writer.add_scalar('Episode reward', np.array(self.reward).sum(), self.episode)
        for j in range(len(self.reward) - 2, -1, -1):
            self.reward[j] += self.reward[j + 1] * GAMMA

    def get_batch(self):
        batch = [[], [], [], []]
        ep_r, ep, lala = 0,0,0
        tmp_batch = [[], [], []]
        while len(batch[0]) < BATCH_SIZE:
            if CONTINUOUS is False:
                action, action_matrix, predicted_action = self.get_action()
            else:
                action, action_matrix, predicted_action = self.get_action_continuous()
            observation, reward, done, info = self.env.step(action)
            self.reward.append(reward)
            ep_r +=reward
            lala +=reward

            tmp_batch[0].append(self.observation)
            tmp_batch[1].append(action_matrix)
            tmp_batch[2].append(predicted_action)
            self.observation = observation

            if done:
                ep +=1
                ep_r = 0
                self.rews.append(lala)
                self.transform_reward()
                for i in range(len(tmp_batch[0])):
                    obs, action, pred = tmp_batch[0][i], tmp_batch[1][i], tmp_batch[2][i]
                    r = self.reward[i]
                    batch[0].append(obs)
                    batch[1].append(action)
                    batch[2].append(pred)
                    batch[3].append(r)
                tmp_batch = [[], [], []]
                print('Episode: %i | Reward: %.2f' % (self.episode,lala))
                if self.episode % 100==0:
                    print("Mean reward of the past 100 episodes: ", str(np.mean(self.rews[-100:])))
                    self.mean_100.append(np.mean(self.rews[-100:]))
                    f = open('results.txt','a')
                    f.write('\n' + str(np.mean(self.rews[-100:])))
                    f.close()                

                self.reset_env()

        obs, action, pred, reward = np.array(batch[0]), np.array(batch[1]), np.array(batch[2]), np.reshape(np.array(batch[3]), (len(batch[3]), 1))
        pred = np.reshape(pred, (pred.shape[0], pred.shape[2]))
        return obs, action, pred, reward

    def run(self):
        while self.episode < EPISODES:
            obs, action, pred, reward = self.get_batch()
            old_prediction = pred
            pred_values = self.critic.predict(obs)

            advantage = reward - pred_values

            actor_loss = []
            critic_loss = []
            for e in range(EPOCHS):
                actor_loss.append(self.actor.train_on_batch([obs, advantage, old_prediction], [action]))
                critic_loss.append(self.critic.train_on_batch([obs], [reward]))
            self.writer.add_scalar('Actor loss', np.mean(actor_loss), self.gradient_steps)
            self.writer.add_scalar('Critic loss', np.mean(critic_loss), self.gradient_steps)

            self.gradient_steps += 1


if __name__ == '__main__':
    ag = Agent()
    ag.run()
    np.save("mean100", ag.mean_100)

    plt.figure(figsize=(10,8))
    plt.tick_params(size=5, labelsize=15)
    plt.plot(ag.mean_100)
    plt.xlabel("100_episodes", fontsize = 13)
    plt.ylabel("Mean_value", fontsize = 13)
    plt.title('Average Reward per 100 episodes', fontsize=15)
    plt.savefig("mean_100.png")


